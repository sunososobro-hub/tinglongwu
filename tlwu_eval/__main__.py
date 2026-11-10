"""`python -m tlwu_eval --smoke [--data DIR] [--out report.json]` (run from the repository folder)."""
from __future__ import annotations

import argparse
import json
import sys

from . import scoring
from .runner import TOTAL_BUDGET_S, Engines, load_manifest, run_smoke


def format_report(rep: dict) -> str:
    lines = []
    for lg in rep["languages"]:
        s, st = lg["seconds"], lg["stages"]
        unit = "CER" if lg["pair"].split("->")[0] in scoring.CHAR_LANGS else "WER"
        tts = "TTS skipped (not installed)" if s["tts"] is None else f"TTS {s['tts']:.1f}s RTF {lg['tts_rtf']} {st['tts']}"
        lines.append(f"{lg['pair']}: load {s['load']:.1f}s | ASR {s['asr']:.1f}s RTF {lg['asr_rtf']} {st['asr']} | "
                     f"MT {s['mt']:.1f}s median {lg['mt_median_s']}s {st['mt']} | {tts} | "
                     f"{lg['elapsed_s']:.0f}s {st['time']} | {unit} {100 * lg['error_rate']:.1f}% chrF {lg['chrf']}")
    lines += [f"{p}: SKIP (no clips in the manifest)" for p in rep["skipped"]]
    mib = (lambda v: "n/a" if v is None else f"{v} MiB")
    lines.append(f"smoke: {'PASS' if rep['pass'] else 'FAIL'} - total {rep['total_s']:.0f}s (budget "
                 f"{TOTAL_BUDGET_S:.0f}s), peak RAM {mib(rep['peak_ram_mib'])}, peak VRAM {mib(rep['peak_vram_mib'])}, "
                 f"overall score {rep['score']['overall']} ({rep['score']['colour']})")
    return "\n".join(lines)


def real_engines(a) -> Engines:
    """The engines `tlwu` itself would use on this machine (same loaders as the CLI)."""
    from tinglongwu import cli, offline
    from tinglongwu.asr import FasterWhisperRecognizer
    from tinglongwu.device import resolve_device
    from tinglongwu.models import models_dir, pick_whisper

    offline.set_mode("listen")  # the eval never needs the network
    mdir = models_dir(a.models_dir)
    dev = resolve_device(a.device)
    model = a.model or pick_whisper("small", mdir)
    return Engines(load_asr=lambda lang: FasterWhisperRecognizer(mdir, model, lang, dev),
                   load_mt=lambda: cli.make_translator(a.engine, mdir, "tw", dev))


def machine_totals():
    from tinglongwu.hwprobe import probe

    hw = probe()
    return hw.ram_mib, hw.vram_mib


def main(argv=None, engines: Engines | None = None, totals=None, **run_kw) -> int:
    """engines / totals / run_kw (clock, ram, vram) are injected by tests; the defaults measure this machine."""
    p = argparse.ArgumentParser(prog="tlwu-eval", description="Local, offline evaluation of tinglongWu.")
    p.add_argument("--smoke", action="store_true", help="5 clips per focus language, per-stage time and PASS/FAIL")
    p.add_argument("--data", default="eval_data/smoke", help="folder with manifest.jsonl and the wav files")
    p.add_argument("--out", default="report.json", help="where the JSON report is written")
    p.add_argument("--model", default=None, help="Whisper size (default: like tlwu)")
    p.add_argument("--engine", default="auto", help="translation engine (default: auto, like tlwu)")
    p.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    p.add_argument("--models-dir", default=None)
    a = p.parse_args(argv)
    if not a.smoke:
        p.error("only --smoke exists so far (the full run is a later part of #57)")
    try:
        rows = load_manifest(a.data)
        rep = run_smoke(rows, engines or real_engines(a), totals=totals or machine_totals(), **run_kw)
    except (FileNotFoundError, ValueError, RuntimeError) as e:  # ModelNotFound is a RuntimeError/FileNotFoundError
        print(f"tlwu-eval: {e}", file=sys.stderr)
        return 2
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    print(format_report(rep))
    print(f"report: {a.out}")
    return 0 if rep["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
