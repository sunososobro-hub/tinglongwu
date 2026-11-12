#!/usr/bin/env python3
"""WER (space-delimited languages) / CER (zh, ja) with partial-coverage alignment.

Usage: python scripts/wer.py REF.txt HYP.txt --lang ru [--partial]
  --partial: hypothesis covers only part of the reference; align it to the
  best-matching reference window (semi-global edit distance) and report coverage.
Pure python. Partial mode is O(len(ref) * len(hyp)) - fine for a few thousand tokens.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata

CHAR_LANGS = {"zh", "ja", "yue"}


def tokenize(text: str, lang: str = "en") -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower()
    text = "".join(c for c in text if not unicodedata.category(c).startswith(("P", "S")))
    if lang in CHAR_LANGS:
        return [c for c in text if not c.isspace()]
    return text.split()


def edit_distance(ref: list, hyp: list) -> int:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1]


def best_window(ref: list, hyp: list) -> tuple[int, int, int]:
    """Sellers algorithm: ref[start:end] minimising edit distance to hyp -> (start, end, errors)."""
    m = len(hyp)
    # column over hyp index j; rows over ref index i. cost[j], start[j] for current ref row.
    cost = list(range(m + 1))
    start = [0] * (m + 1)
    best = (cost[m], 0, 0)  # (errors, start, end) with end = 0 rows consumed
    for i, r in enumerate(ref, 1):
        ncost, nstart = [0], [i]  # free start: matching may begin at any ref position
        for j in range(1, m + 1):
            opts = (
                (cost[j - 1] + (r != hyp[j - 1]), start[j - 1]),  # match/sub
                (cost[j] + 1, start[j]),  # ref token not in hyp (deletion)
                (ncost[j - 1] + 1, nstart[j - 1]),  # extra hyp token (insertion)
            )
            c, s = min(opts, key=lambda o: o[0])
            ncost.append(c)
            nstart.append(s)
        cost, start = ncost, nstart
        if cost[m] < best[0]:
            best = (cost[m], start[m], i)
    return best[1], best[2], best[0]


def score(ref_text: str, hyp_text: str, lang: str = "en", partial: bool = False) -> dict:
    ref, hyp = tokenize(ref_text, lang), tokenize(hyp_text, lang)
    metric = "CER" if lang in CHAR_LANGS else "WER"
    if not ref:
        raise ValueError("empty reference")
    if partial and hyp:
        s, e, errs = best_window(ref, hyp)
        window = ref[s:e]
        return {"metric": metric, "rate": errs / max(len(window), 1), "errors": errs,
                "ref_units": len(window), "hyp_units": len(hyp), "window": (s, e),
                "coverage": len(window) / len(ref)}
    errs = edit_distance(ref, hyp)
    return {"metric": metric, "rate": errs / len(ref), "errors": errs, "ref_units": len(ref),
            "hyp_units": len(hyp), "window": (0, len(ref)), "coverage": 1.0}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("ref")
    p.add_argument("hyp")
    p.add_argument("--lang", default="en")
    p.add_argument("--partial", action="store_true")
    a = p.parse_args(argv)
    rd = lambda f: open(f, encoding="utf-8").read()  # noqa: E731
    r = score(rd(a.ref), rd(a.hyp), a.lang, a.partial)
    print(f"{r['metric']}: {r['rate'] * 100:.2f}%  errors={r['errors']}/{r['ref_units']}  "
          f"hyp_units={r['hyp_units']}  coverage={r['coverage'] * 100:.1f}% "
          f"(ref window {r['window'][0]}..{r['window'][1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
