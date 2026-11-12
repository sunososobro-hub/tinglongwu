#!/usr/bin/env python3
"""Speaker-label evaluation for --speakers (#219/#223).

Usage: python scripts/eval_diarization.py AUDIO_DIR LABELS.csv [--model-dir DIR] [--png out.png]
  LABELS.csv: header-less or headed rows "file,speaker" (file relative to AUDIO_DIR). Rows are fed in
  CSV order, like a conversation, through the same SpeakerLabeler that `tlwu --speakers` uses.
Prints accuracy (x100, 2 decimals; clusters are mapped to true labels by the optimal 1:1 assignment) and the
p50 of per-sentence spk time in seconds, over sentences >= 1 s. Writes a PNG bar chart. No audio is stored in the repo.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import struct
import sys
import zlib
from pathlib import Path

MIN_SECONDS = 1.0
SR = 16000
TARGET_ACC, TARGET_P50 = 85.0, 0.05  # owner's acceptance numbers (#219), drawn as markers in the chart


def best_mapping_accuracy(true, pred) -> float:
    """Percent of items whose cluster, after the best 1:1 cluster->true-label assignment, equals the truth."""
    if not true:
        return 0.0
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    ts, ps = sorted(set(true)), sorted(set(pred))
    m = np.zeros((len(ps), len(ts)), dtype=int)
    for t, p in zip(true, pred):
        m[ps.index(p), ts.index(t)] += 1
    r, c = linear_sum_assignment(-m)
    return 100.0 * m[r, c].sum() / len(true)


def evaluate(items, labeler, clock=None):
    """items: [(true_label, float32 mono 16 kHz seg)] in order. Returns dict(accuracy, p50_s, n, skipped)."""
    true, pred, times, skipped = [], [], [], 0
    for lab, seg in items:
        if len(seg) / SR < MIN_SECONDS:
            skipped += 1
            continue
        name, dt = labeler.label(seg)
        true.append(lab)
        pred.append(name)
        times.append(dt)
    return {"accuracy": round(best_mapping_accuracy(true, pred), 2),
            "p50_s": round(statistics.median(times), 4) if times else 0.0,
            "n": len(true), "skipped": skipped}


def format_report(r: dict) -> str:
    return (f"sentences >= {MIN_SECONDS:g}s: {r['n']} (skipped shorter: {r['skipped']})\n"
            f"accuracy x100: {r['accuracy']:.2f}  (target >= {TARGET_ACC:.2f})\n"
            f"spk p50 s: {r['p50_s']:.4f}  (target <= {TARGET_P50:.2f})")


def write_png(path, r: dict) -> None:
    """Two horizontal bars (accuracy %, p50 as % of its target) on a 400x120 canvas; pure python, no plotting lib."""
    w, h = 400, 120
    px = [[(255, 255, 255)] * w for _ in range(h)]

    def rect(x0, y0, x1, y1, col):
        for y in range(y0, y1):
            for x in range(x0, x1):
                px[y][x] = col

    acc = max(0.0, min(100.0, r["accuracy"]))
    p50 = max(0.0, min(200.0, 100.0 * r["p50_s"] / TARGET_P50)) / 2  # 100 % of target sits at half width
    for y0, frac, ok, mark in ((15, acc / 100, r["accuracy"] >= TARGET_ACC, TARGET_ACC / 100),
                               (65, p50 / 100, r["p50_s"] <= TARGET_P50, 0.5)):
        rect(10, y0, 10 + int(380 * frac), y0 + 30, (60, 160, 80) if ok else (200, 70, 60))
        mx = 10 + int(380 * mark)
        rect(mx, y0 - 5, mx + 2, y0 + 35, (0, 0, 0))
    raw = b"".join(b"\x00" + bytes(c for p in row for c in p) for row in px)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def load_items(audio_dir, labels_csv):
    import numpy as np
    import soundfile as sf

    items = []
    with open(labels_csv, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2 or row[0].strip().lower() == "file":
                continue
            data, sr = sf.read(str(Path(audio_dir) / row[0].strip()), dtype="float32", always_2d=True)
            seg = data.mean(axis=1)
            if sr != SR:
                from scipy.signal import resample_poly
                from math import gcd

                g = gcd(sr, SR)
                seg = resample_poly(seg, SR // g, sr // g).astype(np.float32)
            items.append((row[1].strip(), seg))
    return items


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("audio_dir")
    ap.add_argument("labels_csv")
    ap.add_argument("--model-dir", default="models", help="models dir holding speaker/ (tlwu-setup --speaker-model)")
    ap.add_argument("--png", default="diarization_eval.png")
    a = ap.parse_args(argv)
    from tinglongwu.speaker_embed import make_labeler

    r = evaluate(load_items(a.audio_dir, a.labels_csv), make_labeler(a.model_dir))
    print(format_report(r))
    write_png(a.png, r)
    print(f"chart: {a.png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
