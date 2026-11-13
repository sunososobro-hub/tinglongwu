#!/usr/bin/env python3
"""Score a local talk (wav + reference transcript) through the live pipeline (#274).

Usage: python scripts/eval_talk.py --audio talk.wav --ref transcript_en.txt --to zh [--out result.json]
  --ref: reference transcript, optionally with [mm:ss] / [hh:mm:ss] stamps (stripped before scoring).
  Runs `tlwu --file <audio> --paced --latency-jsonl <tmp> --preset meeting --no-adapt` (real time, no network)
  and scores the native latency records (#435): one JSON line per final line with seconds from the end of speech
  to the segment cut (vad), ASR done (asr), sentence-merge release (merge), translation shown (mt) and total.
Prints and writes JSON: WER x100 (2 decimals) of the record texts, boundary-duplicate count, mid-clause-cut count,
latency p50/p90 s of `total`, the per-stage medians (s) and the command run.
NO EVAL MEDIA IS IN THIS REPO: the talk is licensed CC BY-NC-ND and stays on the owner's machine; pass its paths.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from wer import score  # noqa: E402

STAMP = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?\]")
SENT_END = (".", "?", "!", "…", "...", '."', '?"', '!"', ".)", "。", "？", "！")
MIN_OVERLAP = 3  # words repeated across a line boundary that count as a duplicate


def strip_stamps(text: str) -> str:
    return STAMP.sub(" ", text)


def percentile(values, q: float) -> float:
    """Nearest-rank percentile (q in 0..100); 0.0 for no data."""
    if not values:
        return 0.0
    s = sorted(values)
    return s[max(0, min(len(s) - 1, math.ceil(q / 100 * len(s)) - 1))]


def _words(text: str) -> list[str]:
    return re.sub(r"[^\w\s']", " ", text.lower()).split()


def boundary_duplicates(lines: list[str]) -> int:
    """Count line boundaries where the tail of one line is repeated as the head of the next (>= MIN_OVERLAP words)."""
    n = 0
    for a, b in zip(lines, lines[1:]):
        wa, wb = _words(a), _words(b)
        for k in range(min(len(wa), len(wb), 12), MIN_OVERLAP - 1, -1):
            if wa[-k:] == wb[:k]:
                n += 1
                break
    return n


def mid_clause_cuts(lines: list[str]) -> int:
    """Lines (except the last) that do not end in sentence punctuation: the sentence was cut mid-clause."""
    return sum(1 for ln in lines[:-1] if ln.strip() and not ln.rstrip().endswith(SENT_END))


STAGES = ("vad", "asr", "merge", "mt")


def read_records(path) -> list[dict]:
    """--latency-jsonl file -> list of records (blank lines skipped)."""
    text = Path(path).read_text(encoding="utf-8") if Path(path).exists() else ""
    return [json.loads(ln) for ln in text.splitlines() if ln.strip()]


def latency_summary(records: list[dict]) -> dict:
    """p50/p90 of `total` (nearest rank) and the median of each stage, seconds (3 decimals)."""
    total = [r["total"] for r in records]
    med = lambda v: round(statistics.median(v), 3) if v else 0.0  # noqa: E731
    return {"latency_p50_s": round(percentile(total, 50), 3), "latency_p90_s": round(percentile(total, 90), 3),
            "latency_median_s": med(total),
            "stage_median_s": {k: med([r[k] for r in records if r.get(k) is not None]) for k in STAGES}}


def build_report(ref_text: str, records: list[dict], engines: dict) -> dict:
    lines = [r["text"] for r in records]
    w = score(strip_stamps(ref_text), " ".join(lines), "en")
    return {"wer_x100": round(w["rate"] * 100, 2), "wer_errors": w["errors"], "ref_words": w["ref_units"],
            "boundary_duplicates": boundary_duplicates(lines), "mid_clause_cuts": mid_clause_cuts(lines),
            "lines": len(lines), **latency_summary(records), "engines": engines,
            "note": "no eval media is stored in the repo"}


def tlwu_command(a, jsonl) -> list[str]:
    """`tlwu --file <audio> --paced --latency-jsonl <jsonl> ...` (absolute paths: it runs from the repo root)."""
    md = ["--models-dir", str(Path(a.models_dir).resolve())] if a.models_dir else []
    return [sys.executable, "-m", "tinglongwu", "--file", str(Path(a.audio).resolve()), "--to", a.to,
            "--preset", "meeting", "--no-adapt", "--paced", "--latency-jsonl", str(jsonl)] + md


def run_tlwu(a, run=subprocess.run) -> tuple[list[dict], dict]:
    """Run tlwu on the wav in real time; -> (latency records, engines). Its stderr passes through."""
    with tempfile.TemporaryDirectory() as d:
        jsonl = Path(d) / "latency.jsonl"
        cmd = tlwu_command(a, jsonl)
        rc = run(cmd, stdout=subprocess.DEVNULL, cwd=str(ROOT)).returncode
        if rc != 0:
            raise SystemExit(f"eval_talk: tlwu exited with {rc}")
        records = read_records(jsonl)
    return records, {"preset": "meeting", "cmd": " ".join(cmd[1:])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--to", default="zh")
    ap.add_argument("--out", default="eval_talk.json")
    ap.add_argument("--models-dir")
    a = ap.parse_args(argv)
    ref = Path(a.ref).read_text(encoding="utf-8")
    records, engines = run_tlwu(a)
    rep = build_report(ref, records, engines)
    out = json.dumps(rep, indent=2, ensure_ascii=False)
    print(out)
    Path(a.out).write_text(out + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
