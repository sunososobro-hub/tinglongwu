#!/usr/bin/env python3
"""Bench worker (internal; started by run.py in the right venv). Dev tool, never imported by the product.

  python _worker.py JOB.json
JOB = {"task": "asr"|"mt", "engine": ..., "items": [...], "out": "result.json", "device": "cuda", ...}
Result = {"results": [{"o": output, "s": seconds}, ...], "meta": {...}} (same order as items).
asr items: wav path.  mt items: {"text", "ctx"} (ctx = previous source sentence or null).
Times are per item and exclude model load (load time is in meta).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

EVAL_DIR = Path(os.environ.get("TLWU_EVAL_DIR") or (Path.home() / "tlwu-eval"))
BASE = EVAL_DIR / "llm"
LANG_NAME = {"en": "English", "ru": "Russian", "zh": "Traditional Chinese (繁體中文)"}
MADLAD_TAG = {"en": "<2en>", "zh": "<2zh_Hant>", "ru": "<2ru>"}


def system_for(src: str = "ru", tgt: str = "zh") -> str:
    """The ru->zh prompt is kept byte-identical (cache keys); other pairs use the same wording."""
    if (src, tgt) == ("ru", "zh"):
        return SYSTEM
    tail = (" as written in Taiwan, using natural Taiwan vocabulary and full-width punctuation" if tgt == "zh" else "")
    return (f"You are a professional translator. Translate the user's {LANG_NAME[src].split(' (')[0]} text into "
            f"{LANG_NAME[tgt]}{tail}. Output only the translation, nothing else.")


MADLAD_DIR = BASE / "madlad3b-ct2"
MADLAD_SRC = BASE / "madlad3b-src"
SYSTEM = ("You are a professional translator. Translate the user's Russian text into Traditional Chinese "
          "(繁體中文) as written in Taiwan, using natural Taiwan vocabulary and full-width punctuation. "
          "Output only the translation, nothing else.")


def gpu_used_mib() -> int:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return int(out.strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        return -1


class VramPeak:
    def __init__(self):
        self.base = gpu_used_mib()
        self.peak = self.base
        self._stop = False
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        while not self._stop:
            self.peak = max(self.peak, gpu_used_mib())
            time.sleep(0.4)

    def stop(self) -> int:
        self._stop = True
        self.peak = max(self.peak, gpu_used_mib())
        return self.peak - self.base if self.base >= 0 else -1


def add_torch_dlls():
    """Bench venv: borrow the CUDA 12 DLLs shipped in torch's wheel (cuBLAS, cuDNN) for llama.cpp / CT2."""
    t = Path(sys.prefix) / "Lib" / "site-packages" / "torch" / "lib"
    if t.is_dir():
        os.add_dll_directory(str(t))
        os.environ["PATH"] = str(t) + os.pathsep + os.environ.get("PATH", "")


def timed(fn, items):
    res = []
    for it in items:
        t0 = time.perf_counter()
        o = fn(it)
        res.append({"o": o, "s": time.perf_counter() - t0})
    return res


def job_asr(job):
    import soundfile as sf
    import tinglongwu  # noqa: F401  (offline guard)
    from tinglongwu.asr import FasterWhisperRecognizer
    from tinglongwu.device import resolve_device
    from tinglongwu.models import models_dir

    vr = VramPeak()
    t0 = time.time()
    asr = FasterWhisperRecognizer(models_dir(None), job["model"], "ru", resolve_device(job["device"]))
    load = time.time() - t0

    def one(p):
        x, sr = sf.read(p, dtype="float32")
        assert sr == 16000
        t = asr.transcribe(x)
        return t.text if t else ""

    res = timed(one, job["items"])
    return res, {"load_s": load, "vram_mib": vr.stop(), "device": asr.device}


def job_mt_tlwu(job):
    import tinglongwu  # noqa: F401
    from tinglongwu.device import resolve_device
    from tinglongwu.models import models_dir
    from tinglongwu.translate import PivotTranslator

    vr = VramPeak()
    dev = resolve_device(job["device"])
    t0 = time.time()
    tr = PivotTranslator(models_dir(None), "tw", device=dev)
    src, tgt = job.get("src", "ru"), job.get("tgt", "zh")
    tr.preload(src, tgt)
    load = time.time() - t0
    res = timed(lambda it: tr.translate(it["text"], src, tgt).text.replace("\n", " ") if it["text"].strip() else "",
                job["items"])
    return res, {"load_s": load, "vram_mib": vr.stop(), "device": dev}


def job_mt_madlad(job):
    add_torch_dlls()
    import ctranslate2
    import sentencepiece

    vr = VramPeak()
    t0 = time.time()
    dev = "cuda" if job["device"] != "cpu" else "cpu"
    tr = ctranslate2.Translator(str(MADLAD_DIR), device=dev, compute_type="int8_float16" if dev == "cuda" else "int8")
    sp = sentencepiece.SentencePieceProcessor(model_file=str(MADLAD_DIR / "spiece.model"))
    load = time.time() - t0
    tag = job.get("tag") or MADLAD_TAG[job.get("tgt", "zh")]

    def one(it):
        if not it["text"].strip():
            return ""
        toks = sp.encode(f"{tag} {it['text']}", out_type=str) + ["</s>"]
        r = tr.translate_batch([toks], beam_size=4, max_decoding_length=300)
        return sp.decode(r[0].hypotheses[0]).strip().replace("\n", " ")

    one({"text": "Привет, как дела?"})
    res = timed(one, job["items"])
    return res, {"load_s": load, "vram_mib": vr.stop(), "device": dev}


def main() -> int:
    job = json.loads(Path(sys.argv[1]).read_text("utf-8"))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    eng = job.get("engine", "")
    if job["task"] == "asr":
        res, meta = job_asr(job)
    elif eng == "opus":
        res, meta = job_mt_tlwu(job)
    elif eng.startswith("madlad"):
        res, meta = job_mt_madlad(job)
    else:
        raise SystemExit(f"unknown engine {eng}")
    Path(job["out"]).write_text(json.dumps({"results": res, "meta": meta}, ensure_ascii=False), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
