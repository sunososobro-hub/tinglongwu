"""`python -m tlwu_eval --smoke [--data DIR] [--out report.json] [--tier NAME]` | `--chart OUT.html REPORT.json...`
| `--gate REPORT.json [--baseline eval/baseline.json]`."""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import gate, history, langfloor, scoring, tierchart, tiers
from . import noise as noise_mod
from .runner import CLIPS_PER_LANG, FOCUS_PAIRS, TOTAL_BUDGET_S, Engines, load_extra, load_manifest, run_smoke

NHK_DIR_DEFAULT = r"C:\Users\user\tlwu-eval"  # outside the repo: copyrighted NHK ja clips (#119)


def format_report(rep: dict) -> str:
    lines = []
    for lg in rep["languages"]:
        s, st = lg["seconds"], lg["stages"]
        unit = "CER" if lg["pair"].split("->")[0] in scoring.CHAR_LANGS else "WER"
        tts = "TTS skipped (not installed)" if s["tts"] is None else f"TTS {s['tts']:.1f}s RTF {lg['tts_rtf']} {st['tts']}"
        lines.append(f"{lg['pair']}: load {s['load']:.1f}s | ASR {s['asr']:.1f}s RTF {lg['asr_rtf']} {st['asr']} | "
                     f"MT {s['mt']:.1f}s median {lg['mt_median_s']}s {st['mt']} | {tts} | "
                     f"{lg['elapsed_s']:.0f}s {st['time']} | {unit} {100 * lg['error_rate']:.1f}% chrF {lg['chrf']}"
                     + ("" if lg.get("kana_rate") is None else f" | kana {lg['kana_rate']:.2f} {st['kana']}")
                     + ("" if st.get("vram") == "SKIP" else f" | VRAM {lg['peak_vram_mib']} MiB (budget "
                        f"{lg['vram_budget_mib']}) {st['vram']}"))
    nz = rep.get("noise")
    if nz:
        for cat, e in nz["categories"].items():
            lines.append(f"noise {cat}: {e['count']}/{e['total']} ({e['rate']:.2f}) "
                         + ("FAIL " + "; ".join(repr(t["text"]) for t in e["texts"]) if e["count"] else "PASS"))
        lines.append(f"noise hallucination: {nz['count']}/{nz['total']} ({nz['rate']:.2f}) {nz['stage']}")
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


def main(argv=None, engines: Engines | None = None, totals=None, comet_model=None, **run_kw) -> int:
    """engines / totals / comet_model / run_kw (clock, ram, vram) are injected by tests; defaults measure this machine."""
    p = argparse.ArgumentParser(prog="tlwu-eval", description="Local, offline evaluation of tinglongWu.")
    p.add_argument("--smoke", action="store_true", help="5 clips per focus language, per-stage time and PASS/FAIL")
    p.add_argument("--data", default="eval_data/smoke", help="folder with manifest.jsonl and the wav files")
    p.add_argument("--nhk-dir", default=os.environ.get("TLWU_EVAL_DIR", NHK_DIR_DEFAULT),
                   help="external folder with the NHK ja manifest (default: $TLWU_EVAL_DIR); skipped when absent")
    p.add_argument("--no-noise", action="store_true", help="skip the silence/noise hallucination test (#121)")
    p.add_argument("--noise-dir", default=os.environ.get("TLWU_EVAL_NOISE_DIR", os.path.join(NHK_DIR_DEFAULT, "noise")),
                   help="optional real recordings in <dir>/{silence,aircon,keyboard,distant}/*.wav (default: $TLWU_EVAL_NOISE_DIR)")
    p.add_argument("--out", default="report.json", help="where the JSON report is written")
    p.add_argument("--model", default=None, help="Whisper size (default: like tlwu, or the --tier preset)")
    p.add_argument("--engine", default=None, help="translation engine (default: auto like tlwu, or the --tier preset)")
    p.add_argument("--device", default=None, choices=["auto", "cpu", "cuda"], help="default: auto, or the --tier preset")
    p.add_argument("--models-dir", default=None)
    p.add_argument("--tier", choices=sorted(tiers.TIERS),
                   help="hardware tier preset (#120); adds a 'tier' summary (chrF, COMET, hallucination, latency)")
    p.add_argument("--per-lang", "--clips", type=int, default=None, metavar="N",
                   help=f"clips per language (default {CLIPS_PER_LANG}; the tier baseline uses 20)")
    p.add_argument("--comet", action="store_true",
                   help="also score COMET (needs the optional unbabel-comet package + wmt22-comet-da); with --langs "
                        f"prints COMET x100 per language against the floor {langfloor.COMET_FLOOR:.2f} (#472)")
    p.add_argument("--langs", default=None, metavar="it,pt,vi,th,id",
                   help="source languages to run instead of the focus pairs, each -> zh (zh -> en); #472 Hy-MT2 check")
    p.add_argument("--chart", nargs="+", metavar=("OUT.html", "REPORT.json"),
                   help="draw the tier summaries of these reports side by side into OUT.html")
    p.add_argument("--gate", metavar="REPORT.json",
                   help="compare a --smoke report to the baseline: exit 1 on a chrF or MT latency regression (#361)")
    p.add_argument("--baseline", default="eval/baseline.json",
                   help="baseline for --gate (default: eval/baseline.json); all margins and budgets live there")
    p.add_argument("--history", metavar="FILE.jsonl",
                   help="with --smoke: append one JSON line (date, commit, chrF/MT/ASR per pair, VRAM, hallucinations) (#123)")
    p.add_argument("--trend", metavar="FILE.jsonl", help="draw trend charts from a --history file into --out (default trend.html) (#123)")
    a = p.parse_args(argv)
    if a.trend:
        return history.trend_main(a.trend, "trend.html" if a.out == "report.json" else a.out)
    if a.chart:
        return tierchart.main(p, a.chart)
    if a.gate:
        try:
            code, table = gate.run(a.gate, a.baseline)
        except gate.GateError as e:
            print(f"tlwu-eval: {e}", file=sys.stderr)
            return 2
        print(table)
        return code
    if not a.smoke:
        p.error("only --smoke, --chart and --gate exist so far (the full run is a later part of #57)")
    preset = tiers.TIERS.get(a.tier, {})
    a.model = a.model or preset.get("model")
    a.engine = a.engine or preset.get("engine", "auto")
    a.device = a.device or preset.get("device", "auto")
    run_kw.setdefault("per_lang", a.per_lang or CLIPS_PER_LANG)
    langs = list(dict.fromkeys(x.strip().lower() for x in a.langs.split(",") if x.strip())) if a.langs else None
    if langs:
        run_kw.setdefault("pairs", tuple((x, "en" if x == "zh" else "zh") for x in langs))
        run_kw.setdefault("ja_extra", [])  # the NHK ja group belongs to the focus run, not to a --langs check
    try:
        rows = load_manifest(a.data)
        run_kw.setdefault("ja_extra", load_extra(a.nhk_dir))
        if not a.no_noise:
            run_kw.setdefault("noise", noise_mod.build_clips(a.noise_dir))
        rep = run_smoke(rows, engines or real_engines(a), totals=totals or machine_totals(), **run_kw)
        cm = None
        if a.comet and (a.tier or langs):
            cm = tiers.comet_scores(rep["languages"], comet_model or load_comet(), int(a.device == "cuda"), exact=True)
        if a.tier:
            rep["tier"] = tiers.summarize(rep, a.tier, {k: round(v, 2) for k, v in cm.items()} if cm else None)
        if langs and cm is not None:
            rep["comet_floor"] = langfloor.verdicts(cm, run_kw.get("pairs", FOCUS_PAIRS))
    except (FileNotFoundError, ValueError, RuntimeError) as e:  # ModelNotFound is a RuntimeError/FileNotFoundError
        print(f"tlwu-eval: {e}", file=sys.stderr)
        return 2
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    if a.history:
        history.append(a.history, rep, history.git_commit())
    print(format_report(rep))
    if "tier" in rep:
        print(tiers.summary_line(rep["tier"]))
    if "comet_floor" in rep:
        print(langfloor.table(rep["comet_floor"]))
    print(f"report: {a.out}")
    return 0 if rep["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
