#!/usr/bin/env python
"""Bench the repo's OPUS (Argos/CT2) zh->en path on CPU with scripts/mt_bench_sentences.txt.
  .venv/bin/python scripts/bench_opus_zh_en.py [--models DIR]
Writes ~/mt_bench_opus.tsv (zh \t en \t seconds). Not part of the shipped app."""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=None)
    ap.add_argument("--sentences", default=str(REPO / "scripts" / "mt_bench_sentences.txt"))
    a = ap.parse_args()
    from tinglongwu.models import models_dir
    from tinglongwu.translate import PivotTranslator

    sents = [s.strip() for s in Path(a.sentences).read_text("utf-8").splitlines() if s.strip()]
    t0 = time.perf_counter()
    tr = PivotTranslator(models_dir(a.models), device="cpu")
    tr.preload("zh", "en")
    print(f"load: {time.perf_counter() - t0:.2f}s")
    tr.translate("你好", "zh", "en")  # warm-up, excluded
    rows, lat = [], []
    for zh in sents:
        t = time.perf_counter()
        en = " ".join(tr.translate(zh, "zh", "en").text.split())
        dt = time.perf_counter() - t
        lat.append(dt)
        rows.append((zh, en, dt))
        print(f"{dt:5.2f}s  {zh}  =>  {en}")
    s = sorted(lat)
    print(f"n={len(s)} p50={statistics.median(s):.2f}s p95={s[min(len(s) - 1, round(.95 * (len(s) - 1)))]:.2f}s")
    out = Path.home() / "mt_bench_opus.tsv"
    out.write_text("".join(f"{z}\t{e.replace(chr(9), ' ')}\t{d:.3f}\n" for z, e, d in rows), "utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
