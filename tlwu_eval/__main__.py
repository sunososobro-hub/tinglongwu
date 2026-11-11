"""`python -m tlwu_eval --smoke [--data DIR] [--out report.json] [--tier NAME]` and
`python -m tlwu_eval --chart OUT.html REPORT.json...` (run from the repository folder)."""
from __future__ import annotations

import argparse
import json
import sys

from . import scoring, tiers
from .runner import CLIPS_PER_LANG, TOTAL_BUDGET_S, Engines, load_manifest, run_smoke


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


def load_comet():
    try:
        from comet import download_model, load_from_checkpoint
    except ImportError as e:
        raise RuntimeError("--comet needs the optional unbabel-comet package (pip install unbabel-comet)") from e
    return load_from_checkpoint(download_model("Unbabel/wmt22-comet-da"))


def chart(p, args) -> int:
    out, reports = args[0], args[1:]
    if not out.endswith(".html") or not reports:
        p.error("--chart OUT.html REPORT.json [REPORT.json ...]")
    sums = []
    for path in reports:
        try:
            with open(path, encoding="utf-8") as f:
                rep = json.load(f)
        except (OSError, ValueError) as e:
            print(f"tlwu-eval: cannot read {path}: {e}", file=sys.stderr)
            return 2
        if "tier" not in rep:
            print(f"tlwu-eval: {path} has no tier summary (run it with --tier)", file=sys.stderr)
            return 2
        sums.append(rep["tier"])
    with open(out, "w", encoding="utf-8") as f:
        f.write(tiers.render_chart(sums))
    print(f"chart: {out}")
    return 0


def main(argv=None, engines: Engines | None = None, totals=None, comet_model=None, **run_kw) -> int:
    """engines / totals / comet_model / run_kw (clock, ram, vram) are injected by tests; the defaults measure
    this machine."""
    p = argparse.ArgumentParser(prog="tlwu-eval", description="Local, offline evaluation of tinglongWu.")
    p.add_argument("--smoke", action="store_true", help="5 clips per focus language, per-stage time and PASS/FAIL")
    p.add_argument("--data", default="eval_data/smoke", help="folder with manifest.jsonl and the wav files")
    p.add_argument("--out", default="report.json", help="where the JSON report is written")
    p.add_argument("--model", default=None, help="Whisper size (default: like tlwu, or the --tier preset)")
    p.add_argument("--engine", default=None, help="translation engine (default: auto like tlwu, or the --tier preset)")
    p.add_argument("--device", default=None, choices=["auto", "cpu", "cuda"],
                   help="default: auto, or the --tier preset")
    p.add_argument("--models-dir", default=None)
    p.add_argument("--tier", choices=sorted(tiers.TIERS),
                   help="hardware tier preset (#120); adds a 'tier' summary (chrF, COMET, hallucination, latency)")
    p.add_argument("--per-lang", type=int, default=None, metavar="N",
                   help=f"clips per language (default {CLIPS_PER_LANG}; the tier baseline uses 20)")
    p.add_argument("--comet", action="store_true",
                   help="with --tier: also score COMET (needs the optional unbabel-comet package + wmt22-comet-da)")
    p.add_argument("--chart", nargs="+", metavar=("OUT.html", "REPORT.json"),
                   help="draw the tier summaries of these reports side by side into OUT.html")
    a = p.parse_args(argv)
    if a.chart:
        return chart(p, a.chart)
    if not a.smoke:
        p.error("only --smoke and --chart exist so far (the full run is a later part of #57)")
    preset = tiers.TIERS.get(a.tier, {})
    a.model = a.model or preset.get("model")
    a.engine = a.engine or preset.get("engine", "auto")
    a.device = a.device or preset.get("device", "auto")
    if a.per_lang is not None:
        run_kw.setdefault("per_lang", a.per_lang)
    try:
        rows = load_manifest(a.data)
        rep = run_smoke(rows, engines or real_engines(a), totals=totals or machine_totals(), **run_kw)
        if a.tier:
            comet = tiers.comet_scores(rep["languages"], comet_model or load_comet(), gpus=int(a.device == "cuda")) \
                if a.comet else None
            rep["tier"] = tiers.summarize(rep, a.tier, comet)
    except (FileNotFoundError, ValueError, RuntimeError) as e:  # ModelNotFound is a RuntimeError/FileNotFoundError
        print(f"tlwu-eval: {e}", file=sys.stderr)
        return 2
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    print(format_report(rep))
    if "tier" in rep:
        t = rep["tier"]
        h = t["hallucination"]
        print(f"tier {t['tier']}: chrF {t['chrf']} COMET {t['comet']} hallucination {h['count']}/{h['total']} "
              f"({h['rate']:.2f}) latency median {t['latency_s']['median']:.2f}s p90 {t['latency_s']['p90']:.2f}s "
              f"(baseline {tiers.BASELINE['label']} {tiers.BASELINE['count']}/{tiers.BASELINE['total']})")
    print(f"report: {a.out}")
    return 0 if rep["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
