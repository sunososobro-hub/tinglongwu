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


def run_vosk(a) -> int:
    """CPU path with Vosk-TTS (Apache-2.0, own G2P, no espeak). Fixed built-in voices; a request may carry
    "speaker" to switch voice on the fly. model_path is always local, so vosk never downloads anything."""
    import tempfile
    import wave

    import numpy as np
    from vosk_tts import Model, Synth

    t0 = time.perf_counter()
    synth = Synth(Model(model_path=a.model))
    tmp = os.path.join(tempfile.mkdtemp(prefix="tlwu_vosk_"), "out.wav")
    send({"t": "ready", "load_s": round(time.perf_counter() - t0, 2), "vram_mib": 0, "device": "cpu",
          "engine": "vosk"})
    for line in sys.stdin:
        req = json.loads(line)
        if req.get("cmd") == "quit":
            break
        try:
            t1 = time.perf_counter()
            synth.synth(req["text"], tmp, speaker_id=int(req.get("speaker", a.speaker)))
            with wave.open(tmp, "rb") as w:
                sr = w.getframerate()
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            arr = (pcm.astype(np.float32) / 32768.0).reshape(-1)
            gen = time.perf_counter() - t1
            dur = arr.size / sr
            send({"t": "chunk", "id": req["id"], "sr": sr, "ttfa_s": round(gen, 3)}, arr.tobytes())
            send({"t": "done", "id": req["id"], "audio_s": round(dur, 3), "gen_s": round(gen, 3),
                  "rtf": round(gen / dur, 3) if dur else None, "ttfa_s": round(gen, 3), "peak_vram_mib": 0})
        except Exception as e:  # noqa: BLE001
            send({"t": "error", "id": req.get("id"), "msg": repr(e)[:300]})
    return 0


# Kokoro voice ids 0-4 (--speaker / v0-v4). Kept as a plain literal: tests read it with ast.
KOKORO_VOICES = ["am_michael", "af_heart", "am_adam", "af_bella", "bm_george"]
# Japanese voices for the same ids (best-graded first; Kokoro VOICES.md), used when a request has lang "ja".
KOKORO_JA_VOICES = ["jf_alpha", "jf_gongitsune", "jf_tebukuro", "jf_nezumi", "jm_kumo"]
KOKORO_MODEL_FILE = "kokoro-v1.0.int8.onnx"
KOKORO_VOICES_FILE = "voices-v1.0.bin"


def kokoro_voice(speaker, available=None, warn=lambda m: print(m, file=sys.stderr, flush=True),
                 voices=None) -> str:
    """Voice name for speaker id 0-4 (`voices` = KOKORO_VOICES or KOKORO_JA_VOICES); unknown id or a name missing
    from `available` falls back to the first voice of that list."""
    voices = voices or KOKORO_VOICES
    default = voices[0]
    try:
        name = voices[int(speaker)]
        if int(speaker) < 0:
            raise IndexError
    except (IndexError, ValueError, TypeError):
        warn(f"kokoro: unknown speaker {speaker!r}, using {default}")
        return default
    if available is not None and name not in available:
        warn(f"kokoro: voice {name} not in voices file, using {default}")
        return default
    return name


class JaPhonemizer:
    """Japanese text -> Kokoro phonemes with misaki[ja] (pyopenjtalk + fugashi/unidic). Loaded on the first
    Japanese request only. espeak's own "ja" is unusable (it spells kanji out), so it is never used for ja."""

    def __init__(self):
        self._g2p = None

    def __call__(self, text: str) -> str:
        if self._g2p is None:
            from misaki import ja

            self._g2p = ja.JAG2P()
        return self._g2p(text)[0]


def run_kokoro(a, ja_phonemizer=None) -> int:
    """CPU path with kokoro-onnx (English + Japanese). It bundles GPL espeak, so it only ever runs here, in its own venv.
    Needs kokoro-v1.0.int8.onnx + voices-v1.0.bin in --model; a request may carry "speaker" (0-4)."""
    import numpy as np
    from kokoro_onnx import Kokoro

    mdir = os.path.abspath(a.model)
    onnx, voices = os.path.join(mdir, KOKORO_MODEL_FILE), os.path.join(mdir, KOKORO_VOICES_FILE)
    for f in (onnx, voices):
        if not os.path.isfile(f):
            send({"t": "error", "id": None, "msg": f"missing local file: {f}"})
            return 2
    t0 = time.perf_counter()
    k = Kokoro(onnx, voices)
    ja_ph = ja_phonemizer or JaPhonemizer()
    try:
        avail = set(k.get_voices())
    except Exception:  # noqa: BLE001  (API not present: handle failures per request instead)
        avail = None
    send({"t": "ready", "load_s": round(time.perf_counter() - t0, 2), "vram_mib": 0, "device": "cpu",
          "engine": "kokoro"})
    for line in sys.stdin:
        req = json.loads(line)
        if req.get("cmd") == "quit":
            break
        try:
            t1 = time.perf_counter()
            ja = req.get("lang") == "ja"
            voices = KOKORO_JA_VOICES if ja else KOKORO_VOICES
            voice = kokoro_voice(req.get("speaker", a.speaker), avail, voices=voices)
            if ja:
                kw = dict(text=ja_ph(req["text"]), is_phonemes=True)
            else:
                kw = dict(text=req["text"], lang="en-us")
            try:
                samples, sr = k.create(voice=voice, speed=1.0, **kw)
            except (KeyError, ValueError, AssertionError) as e:
                if voice == voices[0]:
                    raise
                print(f"kokoro: voice {voice} failed ({e!r}), using {voices[0]}", file=sys.stderr, flush=True)
                samples, sr = k.create(voice=voices[0], speed=1.0, **kw)
            arr = np.asarray(samples, dtype=np.float32).reshape(-1)
            gen = time.perf_counter() - t1
            dur = arr.size / sr
            send({"t": "chunk", "id": req["id"], "sr": int(sr), "ttfa_s": round(gen, 3)}, arr.tobytes())
            send({"t": "done", "id": req["id"], "audio_s": round(dur, 3), "gen_s": round(gen, 3),
                  "rtf": round(gen / dur, 3) if dur else None, "ttfa_s": round(gen, 3), "peak_vram_mib": 0})
        except Exception as e:  # noqa: BLE001
            send({"t": "error", "id": req.get("id"), "msg": repr(e)[:300]})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="qwen", choices=["qwen", "vosk", "kokoro"])
    ap.add_argument("--speaker", type=int, default=4,
                    help="vosk: built-in voice id (ru 0.7: 0-4); kokoro: 0-4 (default there: 0)")
    ap.add_argument("--model", required=True, help="local Qwen3-TTS Base dir / Vosk-TTS model dir / Kokoro model dir")
    ap.add_argument("--ref", default="", help="reference wav (qwen)")
    ap.add_argument("--ref-text", default="")
    ap.add_argument("--xvec-only", action="store_true")
    ap.add_argument("--lang", default="Russian")
    ap.add_argument("--chunk", type=int, default=12, help="codec steps per streamed chunk (12 ~ 1 s)")
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"],
                    help="cpu: no CUDA graphs / streaming, plain qwen_tts generation (one chunk per sentence)")
    a = ap.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    if a.engine == "vosk":
        return run_vosk(a)
    if a.engine == "kokoro":
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        return run_kokoro(a)
    if not a.ref:
        ap.error("--ref is required for the qwen engine")
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
