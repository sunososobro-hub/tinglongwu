#!/usr/bin/env python3
"""ASR backend bench (#1753): the same pinned clips through faster-whisper CPU, whisper.cpp CPU and whisper.cpp Vulkan.

Prints, per Whisper size x backend x language, WER (CER for zh / ja) and the ASR real-time factor (transcribe time /
audio time, model load and one warm-up call excluded), plus each backend's licence, library size and model size.
Numbers only; it changes no product default.

  python scripts/bench/make_ci_smoke.py --out eval_data/smoke            # the 5-per-language FLEURS pins (#569)
  tlwu-setup --whisper small --whisper large-v3-turbo --whisper-cpp small --whisper-cpp large-v3-turbo
  python scripts/bench/asr_backend_bench.py --data eval_data/smoke --sizes small,large-v3-turbo \
      --backends fw-cpu,wcpp-cpu,wcpp-vulkan --json asr_backends.json

Backends:
  fw-cpu       tinglongwu.asr.FasterWhisperRecognizer on the CPU (int8, beam 5), what tlwu runs without CUDA today
  wcpp-cpu     the Vulkan pack's whisper.cpp library (tinglongwu.asr_vulkan.WhisperCppBackend) with use_gpu off
  wcpp-vulkan  the same library with use_gpu on (AMD / Intel iGPU through Vulkan)
The whisper.cpp library defaults to the installed Vulkan pack's whisper/ folder; --whisper-lib points at another
build (scripts/build_whisper_vulkan.py --out DIR [--cpu]). A backend that cannot load is reported as unavailable and
the run goes on with the others.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

BACKENDS = ("fw-cpu", "wcpp-cpu", "wcpp-vulkan")
LANGS = ("en", "ru", "ja", "zh")  # the eval/whisper_size_770.json focus languages
CHAR_LANGS = {"zh", "ja", "th"}  # = tlwu_eval.scoring.CHAR_LANGS (CER); kept here so --help needs no import
LICENCES = {"fw-cpu": "MIT (faster-whisper, CTranslate2)", "wcpp-cpu": "MIT (whisper.cpp, ggml)",
            "wcpp-vulkan": "MIT (whisper.cpp, ggml)"}
SAMPLE_RATE = 16000


def read_wav(path) -> tuple[np.ndarray, float]:
    """(float32 16 kHz mono audio, seconds) through the eval's own reader (tlwu_eval.runner.read_wav, the app's
    FileSource): the smoke clips are 32-bit float WAVs, which the stdlib wave module cannot open."""
    from tlwu_eval.runner import read_wav as eval_read

    return eval_read(path)


def load_rows(data_dir, langs) -> list[dict]:
    from tlwu_eval.runner import load_manifest

    return [r for r in load_manifest(data_dir) if r["lang"] in langs]


def _dir_mb(paths) -> float | None:
    total = 0
    for p in paths:
        p = Path(p)
        if p.is_file():
            total += p.stat().st_size
        elif p.is_dir():
            total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return round(total / 2 ** 20, 1) if total else None


def default_lib() -> Path | None:
    from tinglongwu import asr_vulkan, mt_backend

    pack = mt_backend.vulkan_pack_dir()
    return Path(pack) / asr_vulkan.WHISPER_DIR / asr_vulkan.WHISPER_LIB if pack else None


def fw_factory(a):
    """fw-cpu: (load(size, lang) -> transcribe(audio) -> text, info(size) -> {lib_mb, model_mb})."""
    def load(size, lang):
        from tinglongwu.asr import FasterWhisperRecognizer

        rec = FasterWhisperRecognizer(Path(a.models_dir), size, lang, "cpu", threads=a.threads)
        return lambda audio: getattr(rec.transcribe(audio), "text", "") or ""

    def info(size):
        import ctranslate2
        import faster_whisper

        return {"lib_mb": _dir_mb([Path(ctranslate2.__file__).parent, Path(faster_whisper.__file__).parent]),
                "model_mb": _dir_mb([Path(a.models_dir) / f"whisper-{size}"])}
    return load, info


def wcpp_factory(a, use_gpu: bool):
    def lib_path() -> Path:
        lib = Path(a.whisper_lib) if a.whisper_lib else default_lib()
        if lib is None:
            raise ImportError("no Vulkan pack installed and no --whisper-lib given")
        return lib

    def model_path(size) -> Path:
        from tinglongwu.asr_vulkan import MODEL_DIR

        return Path(a.models_dir) / MODEL_DIR / f"ggml-{size}.bin"

    def load(size, lang):
        from tinglongwu.asr_vulkan import WhisperCppBackend

        make = lambda: WhisperCppBackend(lib_path(), model_path(size), use_gpu=use_gpu, threads=a.threads)  # noqa: E731
        if use_gpu:
            be, log = capture_stderr(make)
            check_vulkan(log)
        else:
            be = make()
        return lambda audio: (be.transcribe(audio, lang) or ("",))[0]

    def info(size):
        try:
            lib = lib_path()
        except ImportError:
            lib = None
        return {"lib_mb": _dir_mb([lib]) if lib else None, "model_mb": _dir_mb([model_path(size)])}
    return load, info


def capture_stderr(fn):
    """(fn(), text the native library wrote to fd 2 meanwhile): whisper.cpp logs its backend choice there."""
    import tempfile

    sys.stderr.flush()
    saved = os.dup(2)
    with tempfile.TemporaryFile() as tmp:
        os.dup2(tmp.fileno(), 2)
        try:
            result = fn()
        finally:
            sys.stderr.flush()
            os.dup2(saved, 2)
            os.close(saved)
        tmp.seek(0)
        log = tmp.read().decode("utf-8", "replace")
    sys.stderr.write(log)
    return result, log


def check_vulkan(log: str) -> None:
    """wcpp-vulkan must really run on Vulkan: whisper.cpp logs `whisper_backend_init_gpu: using Vulkan0 backend`, or
    `no GPU found` / `failed to initialize Vulkan0 backend` and silently uses its CPU backend (e.g. a --cpu build). The CPU fallback raises (-> unavailable);
    a log without either line (stderr not capturable on this platform) is accepted and noted."""
    low = log.lower()
    if "no gpu found" in low or "failed to initialize" in low or ("using " in low and " backend" in low and "vulkan" not in low):
        raise RuntimeError("whisper.cpp did not use Vulkan (CPU fallback)")
    if "vulkan" not in low:
        print("asr-bench: wcpp-vulkan: could not confirm the Vulkan backend from the library log", file=sys.stderr)


def _cell_text(s: str) -> str:
    return " ".join(str(s).split()).replace("|", "\\|")


def factories(a) -> dict:
    return {"fw-cpu": fw_factory(a), "wcpp-cpu": wcpp_factory(a, False), "wcpp-vulkan": wcpp_factory(a, True)}


def run_one(load, size: str, lang: str, rows: list[dict], read=read_wav, clock=time.perf_counter) -> dict:
    """One backend x size x language: load, warm up on 1 s of silence, transcribe each clip, score."""
    from tlwu_eval.scoring import error_rate

    t0 = clock()
    transcribe = load(size, lang)
    transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))
    load_s = clock() - t0
    pairs, audio_s, asr_s, clips = [], 0.0, 0.0, []
    for row in rows:
        audio, dur = read(row["wav"])
        t1 = clock()
        hyp = str(transcribe(audio) or "").strip()
        asr_s += clock() - t1
        audio_s += dur
        pairs.append((row["ref_text"], hyp))
        clips.append({"id": row["id"], "hyp": hyp})
    return {"metric": "cer" if lang in CHAR_LANGS else "wer", "error": round(error_rate(pairs, lang), 4),
            "rtf": round(asr_s / audio_s, 4) if audio_s else None, "load_s": round(load_s, 2),
            "audio_s": round(audio_s, 2), "clips": clips}


def bench(a, rows: list[dict], facs: dict, out=print) -> dict:
    res = {"sizes": {}, "backends": {}, "threads": a.threads}
    for size in a.sizes:
        for name in a.backends:
            load, info = facs[name]
            res["backends"].setdefault(name, {"licence": LICENCES[name]})
            try:
                res["backends"][name].setdefault("lib_mb", info(size).get("lib_mb"))
                res["backends"][name][f"model_mb_{size}"] = info(size).get("model_mb")
            except Exception as e:  # noqa: BLE001 - size info is informational only
                out(f"asr-bench: {name} size info: {type(e).__name__}: {e}")
            cell = res["sizes"].setdefault(size, {}).setdefault(name, {})
            for lang in a.langs:
                lr = [r for r in rows if r["lang"] == lang]
                if not lr:
                    continue
                try:
                    cell[lang] = run_one(load, size, lang, lr)
                except Exception as e:  # noqa: BLE001 - missing library / model / GPU: report and go on
                    cell.clear()
                    cell["unavailable"] = f"{type(e).__name__}: {e}"
                    break
                out(f"asr-bench: {size} {name} {lang} {cell[lang]['metric']}={cell[lang]['error']:.3f} "
                    f"rtf={cell[lang]['rtf']}")
    return res


def markdown(res: dict) -> str:
    lines = [f"ASR backends (#1753), threads={res['threads']}; error = WER (CER for zh / ja), rtf = transcribe s / "
             "audio s (load + warm-up excluded)", "", "| size | backend | lang | error | rtf |", "|---|---|---|---|---|"]
    for size, by in res["sizes"].items():
        for name, cell in by.items():
            if "unavailable" in cell:
                lines.append(f"| {size} | {name} | - | unavailable: {_cell_text(cell['unavailable'])} | - |")
                continue
            for lang, r in cell.items():
                lines.append(f"| {size} | {name} | {lang} | {r['metric']} {r['error']:.3f} | {r['rtf']} |")
    lines += ["", "| backend | licence | library MB | model MB |", "|---|---|---|---|"]
    for name, b in res["backends"].items():
        models = ", ".join(f"{k[len('model_mb_'):]} {v}" for k, v in b.items() if k.startswith("model_mb_"))
        lines.append(f"| {name} | {b['licence']} | {b.get('lib_mb')} | {models or '-'} |")
    return "\n".join(lines)


def _csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="folder with manifest.jsonl (make_ci_smoke.py --out)")
    ap.add_argument("--sizes", default="small,large-v3-turbo")
    ap.add_argument("--backends", default=",".join(BACKENDS))
    ap.add_argument("--langs", default=",".join(LANGS))
    ap.add_argument("--models-dir", default=None, help="default: tlwu's models folder (TLWU_MODELS)")
    ap.add_argument("--whisper-lib", default=None, help="whisper.cpp tlwu-whisper library (default: the Vulkan pack's)")
    ap.add_argument("--threads", type=int, default=min(4, os.cpu_count() or 1),
                    help="CPU threads for every backend (default min(4, cores), = WhisperCppBackend's default)")
    ap.add_argument("--json", default=None, help="also write the full result (incl. per-clip text) here")
    a = ap.parse_args(argv)
    a.sizes, a.backends, a.langs = _csv(a.sizes), _csv(a.backends), _csv(a.langs)
    bad = [b for b in a.backends if b not in BACKENDS]
    if bad:
        ap.error(f"unknown backend {', '.join(bad)} (choose from {', '.join(BACKENDS)})")
    return a


def main(argv=None, facs=None, out=print) -> int:
    a = parse_args(argv)
    if a.models_dir is None:
        from tinglongwu.models import models_dir

        a.models_dir = str(models_dir())
    rows = load_rows(a.data, set(a.langs))
    if not rows:
        raise SystemExit(f"no clips for {','.join(a.langs)} in {a.data}")
    res = bench(a, rows, facs or factories(a), out=out)
    out(markdown(res))
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = any("unavailable" not in c for by in res["sizes"].values() for c in by.values())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
