#!/usr/bin/env python3
"""Translate a file of Russian sentences (one per line) with tlwu engines -> one output file per engine.

Runs in the normal tlwu venv (.venv), using the real runtime translators and --device logic.
  python scripts/bench/translate_set.py IN.txt OUTDIR --engines opus,nllb --device cuda --prefix flores
Output: OUTDIR/<prefix>.<engine>.txt (one line per input line) and OUTDIR/<prefix>.<engine>.time (seconds).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import tinglongwu  # noqa: E402,F401  (offline guard on)
from tinglongwu.device import resolve_device  # noqa: E402
from tinglongwu.models import models_dir  # noqa: E402
from tinglongwu.translate import NllbTranslator, PivotTranslator  # noqa: E402


def make(engine: str, mdir: Path, dev: str):
    if engine == "opus":
        return PivotTranslator(mdir, "tw", device=dev)
    if engine == "nllb":
        return NllbTranslator(mdir, "tw", dev)
    raise SystemExit(f"unknown engine {engine}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("infile")
    ap.add_argument("outdir")
    ap.add_argument("--engines", default="opus,nllb")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--prefix", default="out")
    ap.add_argument("--src", default="ru")
    ap.add_argument("--to", default="zh")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)
    lines = Path(a.infile).read_text("utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    lines = lines[:a.limit] if a.limit else lines
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    dev = resolve_device(a.device)
    mdir = models_dir(None)
    for eng in a.engines.split(","):
        try:
            tr = make(eng, mdir, dev)
            tr.preload(a.src, a.to)
        except Exception as e:  # noqa: BLE001
            print(f"[{eng}] unavailable: {e}", file=sys.stderr)
            continue
        t0 = time.time()
        res = [tr.translate(x, a.src, a.to).text.replace("\n", " ") if x.strip() else "" for x in lines]
        dt = time.time() - t0
        (out / f"{a.prefix}.{eng}.txt").write_text("\n".join(res) + "\n", "utf-8")
        (out / f"{a.prefix}.{eng}.time").write_text(f"{dt:.1f}\n")
        print(f"[{eng}] {len(lines)} sentences on {dev}: {dt:.1f}s", flush=True)
        del tr
    return 0


if __name__ == "__main__":
    sys.exit(main())
