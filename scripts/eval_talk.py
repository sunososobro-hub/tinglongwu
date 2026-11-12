#!/usr/bin/env python3
"""Score a local talk (wav + reference transcript) through the live pipeline (#274).

Usage: python scripts/eval_talk.py --audio talk.wav --ref transcript_en.txt --to zh [--out result.json] [--realtime]
  --ref: reference transcript, optionally with [mm:ss] / [hh:mm:ss] stamps (stripped before scoring).
  The audio goes through the same code as `tlwu --file ... --preset meeting --debug-latency` (no network).
Prints and writes JSON: WER x100 (2 decimals), boundary-duplicate count, mid-clause-cut count, latency p50/p90 s
(per output line: ASR ms of its newest segment + queue wait + MT ms, parsed from the --debug-latency log; the
sentence-merge hold is NOT included), asr/mt ms per chunk, and the engines used.
NO EVAL MEDIA IS IN THIS REPO: the talk is licensed CC BY-NC-ND and stays on the owner's machine; pass its paths.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wer import score  # noqa: E402

STAMP = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?\]")
SENT_END = (".", "?", "!", "…", "...", '."', '?"', '!"', ".)", "。", "？", "！")
MIN_OVERLAP = 3  # words repeated across a line boundary that count as a duplicate
ASR_RE = re.compile(r"latency: asr (\d+) ms")
MT_RE = re.compile(r"latency: mt (\d+) ms.*?waited (\d+) ms in queue")


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


def parse_latency_log(text: str):
    """-> (asr_ms per chunk, mt_ms per line, latency seconds per line) from --debug-latency stderr text."""
    asr, mt, lat, last_asr = [], [], [], 0
    for ln in text.splitlines():
        m = ASR_RE.search(ln)
        if m:
            last_asr = int(m.group(1))
            asr.append(last_asr)
            continue
        m = MT_RE.search(ln)
        if m:
            mt.append(int(m.group(1)))
            lat.append((last_asr + int(m.group(1)) + int(m.group(2))) / 1000)
    return asr, mt, lat


def build_report(ref_text: str, lines: list[str], log_text: str, engines: dict) -> dict:
    asr, mt, lat = parse_latency_log(log_text)
    w = score(strip_stamps(ref_text), " ".join(lines), "en")
    med = lambda v: round(statistics.mean(v), 1) if v else 0.0  # noqa: E731
    return {"wer_x100": round(w["rate"] * 100, 2), "wer_errors": w["errors"], "ref_words": w["ref_units"],
            "boundary_duplicates": boundary_duplicates(lines), "mid_clause_cuts": mid_clause_cuts(lines),
            "lines": len(lines), "latency_p50_s": round(percentile(lat, 50), 3),
            "latency_p90_s": round(percentile(lat, 90), 3),
            "asr_ms_per_chunk": med(asr), "mt_ms_per_chunk": med(mt), "engines": engines,
            "note": "no eval media is stored in the repo"}


class _Tee:
    def __init__(self, real):
        self.real, self.buf = real, []

    def write(self, s):
        self.buf.append(s)
        return self.real.write(s)

    def flush(self):
        self.real.flush()

    def __getattr__(self, k):
        return getattr(self.real, k)


def run_pipeline(a):
    """Run the live pipeline on the wav; -> (source lines, stderr log text, engines)."""
    from tinglongwu import cli
    from tinglongwu.sink import Sink

    class Collect(Sink):
        def __init__(self):
            self.lines = []

        def write(self, r):
            self.lines.append(r.original)

    engines: dict = {}
    real_make = cli.make_translator

    def make(*args, **kw):
        t = real_make(*args, **kw)
        engines["mt"] = type(t).__name__
        return t

    cli.make_translator = make
    if a.realtime:
        from tinglongwu.stream import PacedSource

        real_sel = cli.select_backend
        cli.select_backend = lambda *x, **k: PacedSource(real_sel(*x, **k))
    args = cli.parse_args(["--file", a.audio, "--to", a.to, "--preset", "meeting", "--debug-latency",
                           "--no-adapt"] + (["--models-dir", a.models_dir] if a.models_dir else []))
    sink = Collect()
    tee, real_err = _Tee(sys.stderr), sys.stderr
    sys.stderr = tee
    try:
        cli.run_session(args, sink=sink)
    finally:
        sys.stderr = real_err
    engines.update({"asr_model": args.model, "engine_flag": args.engine, "preset": args.preset})
    return sink.lines, "".join(tee.buf), engines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--to", default="zh")
    ap.add_argument("--out", default="eval_talk.json")
    ap.add_argument("--models-dir")
    ap.add_argument("--realtime", action="store_true", help="pace the file at real time (default: as fast as possible)")
    a = ap.parse_args(argv)
    ref = Path(a.ref).read_text(encoding="utf-8")
    lines, log, engines = run_pipeline(a)
    rep = build_report(ref, lines, log, engines)
    out = json.dumps(rep, indent=2, ensure_ascii=False)
    print(out)
    Path(a.out).write_text(out + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
