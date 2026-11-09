#!/usr/bin/env python3
"""FLEURS ru_ru test -> tlwu ASR (per Whisper size) -> WER/CER; also writes the FLORES zho_Hant reference.

  python scripts/bench/fleurs_asr.py --data DATA_DIR --out OUT --models small,medium,large-v3-turbo,large-v3 --n 200 --device cuda
Selection: FLEURS sentences are matched to FLORES-200 rus_Cyrl (dev+devtest) by normalised text; unmatched are dropped;
one utterance per distinct sentence, in file order, first N. Writes OUT/fleurs.ref.txt (zho_Hant), fleurs.src.txt (FLORES ru),
fleurs.gold_ru.txt (FLEURS transcript), OUT/asr_<model>.txt and asr_summary.json. Runs in the tlwu venv.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import soundfile as sf  # noqa: E402
import tinglongwu  # noqa: E402,F401
import wer  # noqa: E402
from tinglongwu.asr import FasterWhisperRecognizer  # noqa: E402
from tinglongwu.device import resolve_device  # noqa: E402
from tinglongwu.models import models_dir  # noqa: E402


def norm(t: str) -> str:
    t = unicodedata.normalize("NFKC", t).lower().replace("ё", "е")
    return re.sub(r"\s+", " ", "".join(c for c in t if c.isalnum() or c.isspace())).strip()


def load_flores(root: Path):
    d = root / "flores200_dataset"
    out = {}
    for split in ("dev", "devtest"):
        ru = (d / split / f"rus_Cyrl.{split}").read_text("utf-8").split("\n")
        zh = (d / split / f"zho_Hant.{split}").read_text("utf-8").split("\n")
        for r, z in zip(ru, zh):
            if r:
                out.setdefault(norm(r), (r, z))
    return out


def select(root: Path, n: int):
    fl = load_flores(root)
    rows, seen, dropped = [], set(), 0
    for line in (root / "fleurs_ru" / "test.tsv").read_text("utf-8").splitlines():
        c = line.split("\t")
        key = norm(c[2])
        if key in seen:
            continue
        if key not in fl:
            dropped += 1
            continue
        seen.add(key)
        rows.append((c[1], c[2], *fl[key]))
        if len(rows) >= n:
            break
    return rows, dropped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--models", default="small,medium,large-v3-turbo,large-v3")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--device", default="auto")
    a = ap.parse_args(argv)
    root, out = Path(a.data), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows, dropped = select(root, a.n)

    def w(name, items):
        (out / name).write_text("\n".join(i.replace("\n", " ") for i in items) + "\n", "utf-8")

    w("fleurs.gold_ru.txt", [r[1] for r in rows])
    w("fleurs.src.txt", [r[2] for r in rows])
    w("fleurs.ref.txt", [r[3] for r in rows])
    index = {p.name: p for p in (root / "fleurs_ru" / "audio").rglob("*.wav")}
    audio = [index[r[0]] for r in rows]
    dev = resolve_device(a.device)
    summ = {"n": len(rows), "unmatched_dropped": dropped}
    for m in a.models.split(","):
        asr = FasterWhisperRecognizer(models_dir(None), m, "ru", dev)
        hyps, dur, t0 = [], 0.0, time.time()
        for p in audio:
            x, sr = sf.read(str(p), dtype="float32")
            assert sr == 16000
            dur += len(x) / sr
            t = asr.transcribe(x)
            hyps.append(t.text if t else "")
        wall = time.time() - t0
        w(f"asr_{m}.txt", hyps)
        e = ref_w = e_c = ref_c = 0
        for r, h in zip(rows, hyps):
            tw, th = wer.tokenize(r[1], "ru"), wer.tokenize(h, "ru")
            e += wer.edit_distance(tw, th)
            ref_w += len(tw)
            cw, ch = wer.tokenize(r[1], "zh"), wer.tokenize(h, "zh")
            e_c += wer.edit_distance(cw, ch)
            ref_c += len(cw)
        summ[m] = {"WER": 100 * e / ref_w, "CER": 100 * e_c / ref_c, "RTF": wall / dur, "audio_s": dur,
                   "device": asr.device}
        print(m, summ[m], flush=True)
        del asr
    (out / "asr_summary.json").write_text(json.dumps(summ, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
