"""TTS worker: run with the tts-venv python (NOT the main venv).  Protocol over stdin/stdout pipes:

  stdin : JSON lines  {"id": n, "text": "..."}   |  {"cmd": "quit"}
  stdout: JSON header line, followed by `n` raw bytes when "n" is present:
          {"t":"ready","load_s":..,"vram_mib":..}
          {"t":"chunk","id":n,"sr":24000,"n":<bytes>,"ttfa_s":..}   + float32 mono PCM
          {"t":"done","id":n,"audio_s":..,"gen_s":..,"rtf":..,"ttfa_s":..}
          {"t":"error","id":n,"msg":".."}
Library prints are diverted to stderr so stdout carries only the protocol.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_OUT = os.fdopen(os.dup(1), "wb", buffering=0)
os.dup2(2, 1)  # anything printed by libraries goes to stderr
sys.stdout = sys.stderr


def send(obj: dict, payload: bytes = b"") -> None:
    if payload:
        obj = {**obj, "n": len(payload)}
    _OUT.write((json.dumps(obj) + "\n").encode("utf-8") + payload)


def run_cpu(a, np, torch) -> int:
    """CPU path: qwen_tts (already in the tts-venv) without CUDA graphs; each sentence is sent as a single chunk."""
    from qwen_tts import Qwen3TTSModel

    torch.set_num_threads(max(1, os.cpu_count() or 1))
    t0 = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(a.model, device_map="cpu", dtype=torch.float32)
    prompt = model.create_voice_clone_prompt(ref_audio=a.ref, ref_text=None if a.xvec_only else a.ref_text,
                                             x_vector_only_mode=a.xvec_only)
    send({"t": "ready", "load_s": round(time.perf_counter() - t0, 2), "vram_mib": 0, "device": "cpu"})
    for line in sys.stdin:
        req = json.loads(line)
        if req.get("cmd") == "quit":
            break
        try:
            t1 = time.perf_counter()
            wavs, sr = model.generate_voice_clone(text=req["text"], language=a.lang, voice_clone_prompt=prompt)
            arr = np.asarray(wavs[0], dtype=np.float32).reshape(-1)
            gen = time.perf_counter() - t1
            dur = arr.size / sr
            send({"t": "chunk", "id": req["id"], "sr": sr, "ttfa_s": round(gen, 3)}, arr.tobytes())
            send({"t": "done", "id": req["id"], "audio_s": round(dur, 3), "gen_s": round(gen, 3),
                  "rtf": round(gen / dur, 3) if dur else None, "ttfa_s": round(gen, 3), "peak_vram_mib": 0})
        except Exception as e:  # noqa: BLE001
            send({"t": "error", "id": req.get("id"), "msg": repr(e)[:300]})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="local Qwen3-TTS Base dir")
    ap.add_argument("--ref", required=True, help="reference wav")
    ap.add_argument("--ref-text", default="")
    ap.add_argument("--xvec-only", action="store_true")
    ap.add_argument("--lang", default="Russian")
    ap.add_argument("--chunk", type=int, default=12, help="codec steps per streamed chunk (12 ~ 1 s)")
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"],
                    help="cpu: no CUDA graphs / streaming, plain qwen_tts generation (one chunk per sentence)")
    a = ap.parse_args()
    if a.device == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
    import numpy as np
    import torch

    if a.device == "cpu":
        return run_cpu(a, np, torch)
    from faster_qwen3_tts import FasterQwen3TTS

    t0 = time.perf_counter()
    model = FasterQwen3TTS.from_pretrained(a.model)
    kw = dict(language=a.lang, ref_audio=a.ref, chunk_size=a.chunk)
    if a.xvec_only:
        kw["xvec_only"] = True
    else:
        kw["ref_text"] = a.ref_text
    # warm-up: captures CUDA graphs so the first real sentence is not penalised
    for _ in model.generate_voice_clone_streaming(text="Привет.", **kw):
        pass
    send({"t": "ready", "load_s": round(time.perf_counter() - t0, 2),
          "vram_mib": round(torch.cuda.max_memory_allocated() / 2**20)})
    for line in sys.stdin:
        req = json.loads(line)
        if req.get("cmd") == "quit":
            break
        try:
            t1, first, total, sr = time.perf_counter(), None, 0, 24000
            for chunk, sr, _timing in model.generate_voice_clone_streaming(text=req["text"], **kw):
                arr = np.asarray(chunk, dtype=np.float32).reshape(-1)
                if first is None:
                    first = time.perf_counter() - t1
                total += arr.size
                send({"t": "chunk", "id": req["id"], "sr": sr, "ttfa_s": round(first, 3)}, arr.tobytes())
            gen = time.perf_counter() - t1
            dur = total / sr
            send({"t": "done", "id": req["id"], "audio_s": round(dur, 3), "gen_s": round(gen, 3),
                  "rtf": round(gen / dur, 3) if dur else None, "ttfa_s": round(first or 0, 3),
                  "peak_vram_mib": round(torch.cuda.max_memory_allocated() / 2**20)})
        except Exception as e:  # noqa: BLE001
            send({"t": "error", "id": req.get("id"), "msg": repr(e)[:300]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
