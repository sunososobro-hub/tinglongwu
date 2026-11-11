#!/usr/bin/env python3
"""Score the FLEURS speech-translation run: (Whisper size x MT engine) chrF/COMET vs FLORES zho_Hant, plus Google refs. .venv-bench.

  python scripts/bench/fleurs_score.py DIR [--no-comet]
DIR = fleurs output dir (fleurs_asr.py + translate_set.py --prefix mt_<model> + google_ref.py outputs).
Writes DIR/e2e_scores.json and prints a markdown table. COMET src = the FLORES Russian sentence (not the ASR text).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wer  # noqa: E402
from sacrebleu.metrics import CHRF  # noqa: E402

ENGINES = ["opus", "nllb", "google"]
SOURCES = ["small", "medium", "large-v3-turbo", "large-v3", "google_stt", "gold"]


def read(p):
    t = Path(p).read_text("utf-8").split("\n")
    return t[:-1] if t and t[-1] == "" else t


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--no-comet", action="store_true")
    a = ap.parse_args(argv)
    d = Path(a.dir)
    src, ref, gold = read(d / "fleurs.src.txt"), read(d / "fleurs.ref.txt"), read(d / "fleurs.gold_ru.txt")
    comet = None
    if not a.no_comet:
        from comet import download_model, load_from_checkpoint

        comet = load_from_checkpoint(download_model("Unbabel/wmt22-comet-da"))
    res = {"asr": {}, "e2e": {}}
    if (d / "asr_google.txt").exists():
        hyp = read(d / "asr_google.txt")
        e = n = ec = nc = 0
        for g, h in zip(gold, hyp):
            tw, th = wer.tokenize(g, "ru"), wer.tokenize(h, "ru")
            e, n = e + wer.edit_distance(tw, th), n + len(tw)
            cw, ch = wer.tokenize(g, "zh"), wer.tokenize(h, "zh")
            ec, nc = ec + wer.edit_distance(cw, ch), nc + len(cw)
        res["asr"]["google_stt"] = {"WER": 100 * e / n, "CER": 100 * ec / nc}
    chrf = CHRF()
    for s in SOURCES:
        for e in ENGINES:
            f = d / f"mt_{s}.{e}.txt"
            if not f.exists():
                continue
            hyp = read(f)
            r = {"chrF": chrf.corpus_score(hyp, [ref]).score}
            if comet:
                r["COMET"] = comet.predict([{"src": x, "mt": m, "ref": t} for x, m, t in zip(src, hyp, ref)],
                                           batch_size=16, gpus=1, progress_bar=False).system_score
            res["e2e"][f"{s}|{e}"] = r
            print(s, e, {k: round(v, 3) for k, v in r.items()}, flush=True)
    (d / "e2e_scores.json").write_text(json.dumps(res, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
