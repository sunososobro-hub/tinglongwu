#!/usr/bin/env python
"""Bench a zh->en GGUF (llama-cpp-python, CPU, offline) on scripts/mt_bench_sentences.txt.
  ~/mt-bench/bin/python scripts/bench_zh_en_mt.py --gguf ~/models/mt/X.gguf --threads 8
Writes ~/mt_bench_hymt.tsv (zh \t en \t seconds). Not part of the shipped app."""
from __future__ import annotations

import argparse
import statistics
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Hy-MT2 official EN template (model card); no system prompt. Sampling per card.
HYMT_PROMPT = ("Translate the following text into English.\n"
               "Note that you should only output the translated result without any additional explanation:\n\n{t}")
HYMT_SAMPLING = dict(temperature=0.7, top_p=0.6, top_k=20, repeat_penalty=1.05)


def pct(xs, q):
    s = sorted(xs)
    return s[min(len(s) - 1, round(q * (len(s) - 1)))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gguf", required=True)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--sentences", default=str(HERE / "mt_bench_sentences.txt"))
    a = ap.parse_args()
    sents = [s.strip() for s in Path(a.sentences).read_text("utf-8").splitlines() if s.strip()]
    from llama_cpp import Llama

    t0 = time.perf_counter()
    llm = Llama(model_path=a.gguf, n_ctx=512, n_threads=a.threads, verbose=False)
    print(f"load: {time.perf_counter() - t0:.2f}s  threads={a.threads}")

    def tr(zh: str) -> str:
        mt = max(32, min(256, 2 * len(zh) + 16))
        r = llm.create_chat_completion([{"role": "user", "content": HYMT_PROMPT.format(t=zh)}],
                                       max_tokens=mt, **HYMT_SAMPLING)
        return r["choices"][0]["message"]["content"]

    tr("你好")  # warm-up, excluded
    rows, lat = [], []
    for zh in sents:
        t = time.perf_counter()
        en = " ".join(tr(zh).split())
        dt = time.perf_counter() - t
        lat.append(dt)
        rows.append((zh, en, dt))
        print(f"{dt:5.2f}s  {zh}  =>  {en}")
    print(f"n={len(lat)} p50={statistics.median(lat):.2f}s p95={pct(lat, .95):.2f}s max={max(lat):.2f}s")
    out = Path.home() / f"mt_bench_hymt.tsv"
    out.write_text("".join(f"{z}\t{e.replace(chr(9), ' ')}\t{d:.3f}\n" for z, e, d in rows), "utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
