#!/usr/bin/env python3
"""Score MT hypotheses against references with chrF, BLEU(zh) and COMET (wmt22-comet-da). Dev tool, run in .venv-bench.

  python scripts/bench/score_mt.py --src SRC.txt --ref REF.txt HYP1.txt HYP2.txt ... [--no-comet] [--json OUT.json]
chrF: sacrebleu CHRF (character n-grams; suits Chinese without tokenisation). BLEU: sacrebleu tokenize=zh.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def read(p):
    t = Path(p).read_text("utf-8").split("\n")
    return t[:-1] if t and t[-1] == "" else t


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--no-comet", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--comet-model", default="Unbabel/wmt22-comet-da")
    ap.add_argument("hyps", nargs="+")
    a = ap.parse_args(argv)
    from sacrebleu.metrics import BLEU, CHRF

    src, ref = read(a.src), read(a.ref)
    comet = None
    if not a.no_comet:
        from comet import download_model, load_from_checkpoint

        comet = load_from_checkpoint(download_model(a.comet_model))
    res = {}
    for h in a.hyps:
        hyp = read(h)
        assert len(hyp) == len(ref) == len(src), (h, len(hyp), len(ref), len(src))
        r = {"chrF": CHRF().corpus_score(hyp, [ref]).score,
             "BLEU_zh": BLEU(tokenize="zh").corpus_score(hyp, [ref]).score}
        if comet:
            data = [{"src": s, "mt": m, "ref": t} for s, m, t in zip(src, hyp, ref)]
            r["COMET"] = comet.predict(data, batch_size=16, gpus=1, progress_bar=False).system_score
        res[Path(h).name] = r
        print(Path(h).name, {k: round(v, 3) for k, v in r.items()}, flush=True)
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
