#!/usr/bin/env python
"""#610 (#595 part 3): CPU-only bake-off of small instruct GGUF models for 3-minute topic summaries.

  python scripts/topic_bakeoff.py --model Qwen3-1.7B=~/models/qwen3-1.7b-q4_k_m.gguf --model Qwen3-4B=... --threads 4 --out r4.json
  python scripts/topic_bakeoff.py --results r4.json r8.json     # merge saved runs into one Markdown table

Per model it reports: load RAM (MB), seconds per topic (prefill + ~120 output tokens), zh/en/ja rubric score on 5 fixed
transcripts, and the p50 delta of a subtitle-like probe workload while the summarizer runs (low priority, CPU only).
The chosen model is the smallest whose subtitle p50 delta is within +10% (smallest = lowest load RAM).
Run it on a 4-core and an 8-core CPU-only machine (use --threads), post the table on #595. Not shipped in the app."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import threading
import time
from pathlib import Path

MAX_P50_DELTA_PCT = 10.0
OUT_TOKENS = 120
PROMPTS = {
    "zh": "請用繁體中文把下列會議片段整理成：一行標題、3 到 5 個重點條列、待辦事項（註明誰：我/對方）。\n\n{t}",
    "en": "Summarize this meeting segment as: a one-line title, 3-5 bullet points, and action items (say who: me/them).\n\n{t}",
    "ja": "次の会議の一部を、タイトル1行、要点3〜5個、担当者（自分/相手）付きのアクション項目にまとめてください。\n\n{t}",
}
# 5 fixed transcripts; rubric = facts that must appear in the summary.
TRANSCRIPTS = [
    {"id": "zh-budget", "lang": "zh",
     "text": "我: 第三季的預算要先確認。對方: 行銷預算是三百萬，研發預算是八百萬，但研發要在十月底前提交明細。"
             "我: 好，我會在週五前把行銷計畫寄給你。對方: 那我負責在十月底前交研發預算明細。",
     "rubric": ["三百萬", "八百萬", "十月底", "週五"]},
    {"id": "zh-release", "lang": "zh",
     "text": "對方: 新版本預計十一月二十日上線，測試還剩兩個嚴重錯誤。我: 我來修登入錯誤，預計週三完成。"
             "對方: 我會通知客服團隊更新說明文件。",
     "rubric": ["十一月二十日", "登入", "週三", "客服"]},
    {"id": "en-hiring", "lang": "en",
     "text": "Me: We need two backend engineers by March. Them: The budget allows one hire now and one after the Q2 review. "
             "Me: I will post the job ad on Monday. Them: I will schedule interviews with the first five candidates.",
     "rubric": ["two backend", "March", "Q2", "Monday"]},
    {"id": "en-outage", "lang": "en",
     "text": "Them: The outage on Tuesday lasted 47 minutes, caused by an expired TLS certificate. Me: We should add expiry alerts "
             "30 days ahead. Them: I will write the postmortem by Thursday. Me: I will set up the alert this week.",
     "rubric": ["47 minutes", "certificate", "30 days", "Thursday"]},
    {"id": "ja-schedule", "lang": "ja",
     "text": "相手: 来月の展示会は12月5日に決まりました。自分: ブースの申し込みは今週中に私が済ませます。"
             "相手: 私はパンフレットを11月28日までに印刷に回します。",
     "rubric": ["12月5日", "ブース", "今週", "11月28日"]},
]


def rubric_score(summary: str, rubric: list[str]) -> float:
    """Fraction of rubric facts present in the summary (case- and whitespace-insensitive)."""
    norm = "".join(summary.lower().split())
    return sum("".join(k.lower().split()) in norm for k in rubric) / len(rubric) if rubric else 0.0


def pct_delta(base: float, under_load: float) -> float:
    return (under_load - base) / base * 100.0 if base > 0 else 0.0


def pick_model(rows: list[dict], max_delta_pct: float = MAX_P50_DELTA_PCT) -> dict | None:
    """Smallest model (lowest load RAM) whose subtitle p50 delta is within +max_delta_pct; None if none qualifies."""
    ok = [r for r in rows if r["subtitle_p50_delta_pct"] <= max_delta_pct]
    return min(ok, key=lambda r: r["load_ram_mb"]) if ok else None


def render_table(runs: dict[str, list[dict]]) -> str:
    """runs: machine label -> rows. One Markdown table per machine plus the chosen model per machine."""
    out = []
    for machine, rows in runs.items():
        out += [f"### {machine}", "",
                "| model | load RAM MB | s / topic | zh | en | ja | subtitle p50 delta |", "|---|---|---|---|---|---|---|"]
        for r in rows:
            q = r["quality"]
            out.append(f"| {r['model']} | {r['load_ram_mb']:.0f} | {r['sec_per_topic']:.1f} | "
                       f"{q.get('zh', 0):.2f} | {q.get('en', 0):.2f} | {q.get('ja', 0):.2f} | {r['subtitle_p50_delta_pct']:+.1f}% |")
        best = pick_model(rows)
        out += ["", f"Chosen ({machine}): " + (f"**{best['model']}** (smallest with subtitle p50 within +{MAX_P50_DELTA_PCT:.0f}%)"
                                               if best else f"none within +{MAX_P50_DELTA_PCT:.0f}%"), ""]
    return "\n".join(out)


def probe_once() -> None:
    """One subtitle-like CPU unit of work (stand-in for a short ASR/MT step), roughly 10-30 ms."""
    h = b"tlwu"
    for _ in range(20000):
        h = hashlib.sha256(h).digest()


def measure_probe(stop: threading.Event | None, n: int = 40, gap_s: float = 0.05, work=probe_once) -> float:
    """Median seconds of `work` over up to n runs (stops early when `stop` is set); gap_s sleeps between runs."""
    lat = []
    for _ in range(n):
        if stop is not None and stop.is_set():
            break
        t = time.perf_counter()
        work()
        lat.append(time.perf_counter() - t)
        time.sleep(gap_s)
    return statistics.median(lat) if lat else 0.0


def rss_mb() -> float:
    import psutil
    return psutil.Process().memory_info().rss / 1e6


def lower_priority() -> None:
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)
    except Exception:
        pass


def bench_model(name: str, summarize, load_ram_mb: float, probe=measure_probe) -> dict:
    """summarize(lang, text) -> str. Times each transcript, scores the rubric, measures probe p50 with/without load."""
    base = probe(None)
    secs: list[float] = []
    per_lang: dict[str, list[float]] = {}
    done = threading.Event()

    def worker():
        try:
            for tr in TRANSCRIPTS:
                t = time.perf_counter()
                s = summarize(tr["lang"], tr["text"])
                secs.append(time.perf_counter() - t)
                per_lang.setdefault(tr["lang"], []).append(rubric_score(s, tr["rubric"]))
        finally:
            done.set()

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    loaded = probe(done)
    th.join()
    return {"model": name, "load_ram_mb": load_ram_mb, "sec_per_topic": statistics.mean(secs) if secs else 0.0,
            "quality": {k: statistics.mean(v) for k, v in per_lang.items()},
            "subtitle_p50_base_s": base, "subtitle_p50_loaded_s": loaded,
            "subtitle_p50_delta_pct": pct_delta(base, loaded)}


def run_llama(name: str, gguf: str, threads: int) -> dict:
    from llama_cpp import Llama
    before = rss_mb()
    llm = Llama(model_path=gguf, n_ctx=2048, n_threads=threads, verbose=False)
    ram = rss_mb() - before
    lower_priority()

    def summarize(lang: str, text: str) -> str:
        r = llm.create_chat_completion([{"role": "user", "content": PROMPTS[lang].format(t=text)}],
                                       max_tokens=OUT_TOKENS, temperature=0.2)
        return r["choices"][0]["message"]["content"]

    summarize("en", "Me: hello. Them: hi.")  # warm-up, excluded
    return bench_model(name, summarize, ram)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", action="append", default=[], metavar="NAME=GGUF", help="repeat for each model (Q4_K_M GGUF)")
    ap.add_argument("--threads", type=int, default=os.cpu_count() or 4, help="llama.cpp threads (4 / 8 for the two machines)")
    ap.add_argument("--machine", default=None, help="label for the table, e.g. '4-core CPU'")
    ap.add_argument("--out", default=None, help="write this run's rows as JSON")
    ap.add_argument("--results", nargs="+", metavar="JSON", help="skip running; merge saved --out files into the table")
    a = ap.parse_args(argv)
    if a.results:
        runs = {}
        for p in a.results:
            d = json.loads(Path(p).read_text("utf-8"))
            runs[d["machine"]] = d["rows"]
        print(render_table(runs))
        return 0
    if not a.model:
        ap.error("give at least one --model NAME=GGUF (or --results)")
    rows = []
    for spec in a.model:
        name, _, path = spec.partition("=")
        if not path or not Path(path).expanduser().is_file():
            ap.error(f"--model {spec!r}: expected NAME=path-to.gguf")
        print(f"[{name}] running ...", flush=True)
        rows.append(run_llama(name, str(Path(path).expanduser()), a.threads))
    machine = a.machine or f"{a.threads}-thread CPU"
    if a.out:
        Path(a.out).write_text(json.dumps({"machine": machine, "rows": rows}, indent=1), "utf-8")
    print(render_table({machine: rows}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
