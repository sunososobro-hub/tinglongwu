#!/usr/bin/env python3
"""Dev tool (#602): turn a real `tlwu_eval --smoke --langs vi,th,id,es,de,fr --per-lang 20` run into the committed
tier-2 score file eval/tier2_report.json (one row per language: clips, chrF, ASR error rate, MT median) and print it
as a Markdown table.

  python scripts/bench/make_ci_smoke.py --pins eval/tier2_pins.json --out eval_data/tier2
  python -m tlwu_eval --smoke --data eval_data/tier2 --langs vi,th,id,es,de,fr --per-lang 20 --no-noise \
      --model large-v3-turbo --engine hymt --device cuda --out eval_data/tier2_run.json
  python scripts/bench/tier2_report.py eval_data/tier2_run.json --model large-v3-turbo --engine hymt --device cuda

The smoke PASS/FAIL (speed budgets for 5 clips) is not carried over: this file records quality per language only.
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_FILE = ROOT / "eval" / "tier2_report.json"
PIN_FILE = ROOT / "eval" / "tier2_pins.json"
TIER2 = ("vi", "th", "id", "es", "de", "fr")


def rows(rep: dict) -> dict:
    """pair -> {clips, chrf, asr_error_rate, mt_median_s} for every tier-2 pair in a tlwu_eval smoke report.
    Raises ValueError when a tier-2 language is missing from the report."""
    by_pair = {lg["pair"]: lg for lg in rep["languages"]}
    missing = [f"{x}->zh" for x in TIER2 if f"{x}->zh" not in by_pair]
    if missing:
        raise ValueError(f"report has no rows for {', '.join(missing)}")
    return {f"{x}->zh": {"clips": len(by_pair[f"{x}->zh"]["clips"]), "chrf": by_pair[f"{x}->zh"]["chrf"],
                         "asr_error_rate": by_pair[f"{x}->zh"]["error_rate"],
                         "mt_median_s": by_pair[f"{x}->zh"]["mt_median_s"]} for x in TIER2}


def table(pairs: dict) -> str:
    lines = ["| pair | clips | chrF | ASR WER/CER | MT median s |", "|---|---|---|---|---|"]
    for pair, r in pairs.items():
        lines.append(f"| {pair} | {r['clips']} | {r['chrf']} | {100 * r['asr_error_rate']:.1f}% | {r['mt_median_s']} |")
    return "\n".join(lines)


def build(rep: dict, run: dict) -> dict:
    pin = json.loads(PIN_FILE.read_text("utf-8"))
    return {"_note": "Tier-2 scores (#602) from a real tlwu_eval run on the pinned FLEURS clips in eval/tier2_pins.json "
                     "(FLEURS test, CC-BY 4.0; ref_zh = cmn_hans_cn transcript of the same FLoRes id, Taiwan "
                     "Traditional). Rebuild with scripts/bench/tier2_report.py; docs/support.json cells stay as they "
                     "are unless an owner decision uses these numbers.",
            "run": {**run, "pins": "eval/tier2_pins.json", "fleurs_revision": pin["revision"]},
            "pairs": rows(rep)}


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT,
                              timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("report", help="tlwu_eval --smoke --langs ... report JSON")
    ap.add_argument("--model", required=True)
    ap.add_argument("--engine", required=True)
    ap.add_argument("--device", required=True)
    ap.add_argument("--out", default=str(OUT_FILE))
    a = ap.parse_args(argv)
    rep = json.loads(Path(a.report).read_text("utf-8"))
    run = {"date": datetime.date.today().isoformat(), "commit": git_commit(), "model": a.model, "engine": a.engine,
           "device": a.device}
    try:
        out = build(rep, run)
    except ValueError as e:
        print(f"tier2_report: {e}", file=sys.stderr)
        return 1
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(table(out["pairs"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
