#!/usr/bin/env python3
"""Pick ~N Russian ASR sentences from a tlwu --transcript-only file and align each to the matching clean reference text.

  python scripts/bench/prep_units.py ASR.txt REF.txt OUTDIR [--n 40]
Writes OUTDIR/units.asr.txt and OUTDIR/units.ref.txt (one sentence per line, line i of both = same passage).
Sentences come from tlwu's SentenceMerger (same logic as the runtime), 8..40 words, evenly spaced through the episode.
The reference window is found with a semi-global word alignment (scripts/wer.py best_window).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wer  # noqa: E402
from tinglongwu.merge import SentenceMerger  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("asr")
    ap.add_argument("ref")
    ap.add_argument("outdir")
    ap.add_argument("--n", type=int, default=40)
    a = ap.parse_args(argv)
    m, units = SentenceMerger(), []
    for i, line in enumerate(Path(a.asr).read_text("utf-8").splitlines()):
        if line.strip():
            units += [u.text for u in m.add(line, "ru", float(i))]
    units += [u.text for u in m.flush()]
    good = [u for u in units if 8 <= len(u.split()) <= 40]
    step = max(1, len(good) // a.n)
    pick = good[step // 2::step][:a.n]
    # clean reference as (original word, normalised token) pairs
    words = Path(a.ref).read_text("utf-8").split()
    pairs = [(w, wer.tokenize(w, "ru")) for w in words]
    pairs = [(w, t[0]) for w, t in pairs if t]
    toks = [t for _, t in pairs]
    refs = []
    for u in pick:
        s, e, _ = wer.best_window(toks, wer.tokenize(u, "ru"))
        refs.append(" ".join(w for w, _ in pairs[s:e]))
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "units.asr.txt").write_text("\n".join(pick) + "\n", "utf-8")
    (out / "units.ref.txt").write_text("\n".join(refs) + "\n", "utf-8")
    print(f"{len(units)} units, {len(good)} in 8..40 words, picked {len(pick)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
