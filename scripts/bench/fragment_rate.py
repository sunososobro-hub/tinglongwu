#!/usr/bin/env python3
"""#389: fragment rate and added latency of SentenceMerger --min-words, replaying ASR segments (no models needed).

  python scripts/bench/fragment_rate.py SEGMENTS.txt [--lang ru] [--min-words 3] [--max-wait 6] [--gap 2]
SEGMENTS.txt: one ASR segment per line, optionally "<seconds>\\t<text>" (arrival time); without a time the
segments arrive every --gap seconds. Prints, for min-words 0 (off) and N: MT units, fragment rate (share of units
with fewer than N words, a CJK character counting as one) and the added release latency per segment (p50 / max, s).
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tinglongwu.merge import SentenceMerger  # noqa: E402


def load(path: str, gap: float) -> list[tuple[float, str]]:
    segs = []
    for i, line in enumerate(x for x in Path(path).read_text("utf-8").splitlines() if x.strip()):
        t, sep, text = line.partition("\t")
        segs.append((float(t), text) if sep else (i * gap, line))
    return segs


def replay(segs, lang: str, min_words: int, max_wait: float, step: float = 0.1):
    """-> (unit texts, release time of each segment: when the first unit holding its text came out)."""
    m = SentenceMerger(max_wait=max_wait, min_words=min_words)
    units, released, now = [], {}, 0.0

    def take(us, t):
        for u in us:
            units.append(u.text)
            for i in range(len(segs)):
                if i <= u.tag:
                    released.setdefault(i, t)

    for i, (t, text) in enumerate(segs):
        while now + step <= t:  # the pipeline polls due() between segments
            now += step
            take(m.due(now), now)
        now = t
        take(m.add(text, lang, t, pause=0.0, tag=i), t)
    end = segs[-1][0] if segs else 0.0
    while m.buf and now < end + max_wait + step:
        now += step
        take(m.due(now), now)
    take(m.flush(), now)
    return units, [released[i] for i in range(len(segs))]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("segments")
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--min-words", type=int, default=3)
    ap.add_argument("--max-wait", type=float, default=6.0)
    ap.add_argument("--gap", type=float, default=2.0)
    a = ap.parse_args(argv)
    segs = load(a.segments, a.gap)
    probe = SentenceMerger(min_words=a.min_words)
    base_units, base_t = replay(segs, a.lang, 0, a.max_wait)
    for n, (units, rel) in ((0, (base_units, base_t)), (a.min_words, replay(segs, a.lang, a.min_words, a.max_wait))):
        frag = sum(probe._is_fragment(u) for u in units) / max(1, len(units))
        added = [r - b for r, b in zip(rel, base_t)]
        print(f"min_words={n}: units={len(units)} fragment_rate={frag:.1%} "
              f"added_latency_p50={statistics.median(added) if added else 0:.2f}s max={max(added, default=0):.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
