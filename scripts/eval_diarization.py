#!/usr/bin/env python3
"""Speaker-label evaluation for --speakers (#219/#223).

Usage: python scripts/eval_diarization.py AUDIO_DIR LABELS.csv [--model-dir DIR] [--png out.png]
       python scripts/eval_diarization.py --transcript SESSION.jsonl   (#384: a --save file with speaker fixes)
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from anywhere: the tinglongwu package

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
    """items: [(true_label, float32 mono 16 kHz seg)] in order. Returns dict(accuracy, p50_s, n, skipped, speakers,
    labels, labels_raw). #1256: labels = distinct labels a user sees at the end (a label merged into an older one by
    the clusterer counts as that one; accuracy is scored on these too); labels_raw = distinct labels ever handed out;
    speakers = distinct true speakers among the scored sentences."""
    true, pred, times, skipped = [], [], [], 0
    for lab, seg in items:
        if len(seg) / SR < MIN_SECONDS:
            skipped += 1
            continue
        name, dt = labeler.label(seg)
        true.append(lab)
        pred.append(name)
        times.append(dt)
    clusterer = getattr(labeler, "clusterer", None)
    seen = [clusterer.kept(p) if clusterer is not None else p for p in pred]
    from tinglongwu.speakers import UNKNOWN

    return {"accuracy": round(best_mapping_accuracy(true, seen), 2),
            "p50_s": round(statistics.median(times), 4) if times else 0.0,
            "n": len(true), "skipped": skipped, "speakers": len(set(true)),
            "labels": len({p for p in seen if p and p != UNKNOWN}),
            "labels_raw": len({p for p in pred if p and p != UNKNOWN})}


def format_report(r: dict) -> str:
    out = (f"sentences >= {MIN_SECONDS:g}s: {r['n']} (skipped shorter: {r['skipped']})\n"
           f"accuracy x100: {r['accuracy']:.2f}  (target >= {TARGET_ACC:.2f})\n"
           f"spk p50 s: {r['p50_s']:.4f}  (target <= {TARGET_P50:.2f})")
    if "labels" in r:
        out += (f"\nspeakers: {r['speakers']}  labels: {r['labels']} (handed out before merges: {r['labels_raw']})  "
                f"ratio: {label_ratio(r):.2f}")
    return out


def label_ratio(r: dict) -> float:
    """#1256: labels a user sees per real speaker (0 when no speaker was scored)."""
    return r["labels"] / r["speakers"] if r.get("speakers") else 0.0


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


def score_transcript(path) -> dict:
    """#384: accuracy of the auto speaker labels in a --save JSONL against the user's corrections in it.

    Result lines carry "speaker" (auto label); "fix" lines are replayed in order through SpeakerBook (rename /
    relabel / merge; a relabel index counts result lines from 0). Lines without an auto label are not scored."""
    import json

    from tinglongwu.speakers import SpeakerBook

    book = SpeakerBook()
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if "fix" in rec:
                book.apply(rec["fix"])
            elif "src" in rec:
                book.add(rec.get("speaker"))
    pairs = [(book.corrected(i), auto) for i, (auto, _fixed) in enumerate(book.lines) if auto]
    return {"accuracy": round(best_mapping_accuracy([t for t, _ in pairs], [p for _, p in pairs]), 2),
            "n": len(pairs), "corrected": sum(1 for t, p in pairs if t != p)}


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
    ap.add_argument("audio_dir", nargs="?")
    ap.add_argument("labels_csv", nargs="?")
    ap.add_argument("--transcript", help="score a --save JSONL with speaker corrections instead (#384)")
    ap.add_argument("--model-dir", default="models", help="models dir holding speaker/ (tlwu-setup --speaker-model)")
    ap.add_argument("--png", default="diarization_eval.png")
    ap.add_argument("--max-label-ratio", type=float, default=0.0,
                    help="#1256, audio mode only: exit 1 when labels / real speakers is above this, or when no "
                         "label was produced (0 = report only)")
    ap.add_argument("--min-speakers", type=int, default=0,
                    help="#1256, with --max-label-ratio: also fail when fewer real speakers were scored")
    a = ap.parse_args(argv)
    import math

    if not math.isfinite(a.max_label_ratio) or a.max_label_ratio < 0:
        ap.error("--max-label-ratio must be a finite number >= 0")
    if a.transcript and a.max_label_ratio:
        ap.error("--max-label-ratio applies to AUDIO_DIR mode, not --transcript")
    if a.transcript:
        r = score_transcript(a.transcript)
        print(f"lines with a speaker: {r['n']} (corrected by the user: {r['corrected']})\n"
              f"accuracy x100: {r['accuracy']:.2f}  (target >= {TARGET_ACC:.2f})")
        return 0
    if not (a.audio_dir and a.labels_csv):
        ap.error("AUDIO_DIR and LABELS.csv are required without --transcript")
    from tinglongwu.speaker_embed import make_labeler

    r = evaluate(load_items(a.audio_dir, a.labels_csv), make_labeler(a.model_dir))
    print(format_report(r))
    write_png(a.png, r)
    print(f"chart: {a.png}")
    if a.max_label_ratio:
        why = None
        if r["speakers"] < max(1, a.min_speakers):
            why = f"only {r['speakers']} speakers scored, need {max(1, a.min_speakers)}"
        elif not r["labels"]:
            why = "no speaker label produced"
        elif label_ratio(r) > a.max_label_ratio:
            why = f"{label_ratio(r):.2f}x > {a.max_label_ratio:g}x"
        if why:
            print(f"FAIL: {r['labels']} labels for {r['speakers']} speakers ({why}, #1256)")
            return 1
        print(f"PASS: {r['labels']} labels for {r['speakers']} speakers ({label_ratio(r):.2f}x <= "
              f"{a.max_label_ratio:g}x, #1256)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
