#!/usr/bin/env python3
"""#1198: Hy-MT2 CPU translation time per line under the live load, for several thread plans.

In the live app the speech model (Whisper, CTranslate2) and the translation model (Hy-MT2, llama.cpp) run at the
same time on different threads: Whisper transcribes the next segment while Hy-MT2 translates the previous one.
`tlwu_eval --smoke` runs them one after the other, so it never shows what happens when both thread pools together
ask for more threads than the CPU has (#1198: 7.6 s per line on an 8-core / 16-thread Ryzen 7040).

This bench translates the gold source lines of the smoke set (no ASR errors, so chrF compares only the MT path)
while a background thread keeps Whisper busy on the smoke clips, once per thread plan, and prints one Markdown
table: MT median / p90 seconds per line, chrF against the references, and Whisper seconds per clip during the run.

  python scripts/bench/mt_cpu_threads.py --data eval_data/smoke --models-dir ~/tlwu-models [--whisper small]
  python scripts/bench/mt_cpu_threads.py ... --plans old,cpu-mode --no-asr     # MT alone, no background Whisper

Plans (asr / mt decode / mt batch threads; P = physical cores, L = logical CPUs):
  old       the pre-#1198 CPU mode: asr = min(L-1, 8), mt = L//2, batch = L (llama-cpp-python defaults)
  cpu-mode  tinglongwu.hwprobe.cpu_mode_threads(L): what tlwu uses now (asr = L//2, mt = batch = L - asr)
  physical  asr = max(1, P//2), mt = batch = max(1, P - asr)
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PAIRS = (("en", "zh"), ("ja", "zh"), ("ru", "zh"), ("zh", "en"))


def plans(physical: int, logical: int) -> dict[str, tuple[int, int, int]]:
    from tinglongwu.hwprobe import cpu_mode_threads

    P, L = max(1, physical), max(1, logical)
    cm = cpu_mode_threads(L)
    return {"old": (max(1, min(L - 1, 8)), max(1, L // 2), L),
            "cpu-mode": (cm.asr, cm.mt, cm.mt_batch),
            "physical": (max(1, P // 2), max(1, P - P // 2), max(1, P - P // 2))}


def mt_lines(rows: list[dict], per_pair: int) -> list[tuple[str, str, str, str]]:
    """(src, tgt, source text, reference) for the focus pairs, gold transcripts as input."""
    out = []
    for src, tgt in PAIRS:
        got = [r for r in rows if r.get("lang") == src and r.get(f"ref_{tgt}") and r.get("ref_text")][:per_pair]
        out += [(src, tgt, r["ref_text"], r[f"ref_{tgt}"]) for r in got]
    return out


class AsrLoad:
    """Keeps Whisper (beam 1, like CPU mode) transcribing smoke clips on its own thread until stopped."""

    def __init__(self, mdir: Path, size: str, threads: int, wavs: list[str]):
        from tinglongwu.asr import FasterWhisperRecognizer
        from tlwu_eval.runner import read_wav

        self.asr = FasterWhisperRecognizer(mdir, size, "auto", "cpu", beam=1, threads=threads)
        self.audio = [read_wav(w)[0] for w in wavs]
        self.stop = threading.Event()
        self.spans: list[tuple[float, float]] = []  # (start, end) perf_counter of each clip
        self.error: BaseException | None = None
        self.t = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        i = 0
        try:
            while not self.stop.is_set():
                t0 = time.perf_counter()
                self.asr.transcribe(self.audio[i % len(self.audio)])
                self.spans.append((t0, time.perf_counter()))
                i += 1
        except BaseException as e:  # noqa: BLE001 - re-raised by __exit__: a bench without its load is invalid
            self.error = e

    def during(self, start: float, end: float) -> list[float]:
        """Seconds per clip for clips that ran entirely inside [start, end] (the MT measurement window)."""
        return [b - a for a, b in self.spans if a >= start and b <= end]

    def __enter__(self):
        self.asr.transcribe(self.audio[0])  # warm-up outside the measurement
        self.t.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.t.join()
        if self.error is not None and exc[0] is None:
            raise RuntimeError(f"background Whisper failed, MT numbers are not under load: {self.error}") \
                from self.error


def translate_all(llm, lines) -> tuple[list[float], list[tuple[str, str]]]:
    from tinglongwu.engines import clean_output, max_tokens_for
    from tinglongwu.hymt import build_prompt, sampling, zh_convert

    times, pairs = [], []
    for src, tgt, text, ref in lines:
        t0 = time.perf_counter()
        r = llm.create_chat_completion([{"role": "user", "content": build_prompt(text, src, tgt, [])}],
                                       max_tokens=max_tokens_for(text), **sampling())
        times.append(time.perf_counter() - t0)
        out = clean_output(r["choices"][0]["message"]["content"] or "")
        pairs.append((ref, zh_convert(out, "tw") if tgt == "zh" else out))
    return times, pairs


def p90(v: list[float]) -> float:
    s = sorted(v)
    return s[min(len(s) - 1, int(round(0.9 * (len(s) - 1))))]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--data", default="eval_data/smoke")
    p.add_argument("--models-dir", default=os.environ.get("TLWU_MODELS"))
    p.add_argument("--whisper", default="small")
    p.add_argument("--plans", default="old,cpu-mode,physical")
    p.add_argument("--per-pair", type=int, default=5)
    p.add_argument("--no-asr", action="store_true", help="MT alone (no background Whisper)")
    a = p.parse_args(argv)
    # The plans are listen-only thread counts: TLWU_BOTH=1 would make Whisper use the #250 listen+speak split instead
    # of the counts printed in the table.
    os.environ.pop("TLWU_BOTH", None)

    import psutil
    from llama_cpp import Llama
    from tinglongwu.hymt import hymt_path
    from tinglongwu.models import models_dir
    from tlwu_eval import scoring
    from tlwu_eval.runner import load_manifest

    mdir = models_dir(a.models_dir)
    L = os.cpu_count() or 1
    P = psutil.cpu_count(logical=False) or L
    rows = load_manifest(a.data)
    lines = mt_lines(rows, a.per_pair)
    wavs = [r["wav"] for r in rows if r.get("wav")][:4]
    if not lines:
        print("mt_cpu_threads: no gold lines with references in the manifest", file=sys.stderr)
        return 2
    allp = plans(P, L)
    print(f"CPU: {P} physical cores, {L} logical; {len(lines)} lines; background Whisper "
          f"{'off' if a.no_asr else a.whisper + ' beam 1'}\n")
    out = ["| plan | asr / mt / mt batch threads | MT median s/line | MT p90 s | chrF | Whisper s/clip (during MT) |",
           "|---|---|---|---|---|---|"]
    seen = set()
    for name in [x.strip() for x in a.plans.split(",") if x.strip()]:
        asr_t, mt_t, mt_b = allp[name]
        if (asr_t, mt_t, mt_b) in seen:
            out.append(f"| {name} | {asr_t} / {mt_t} / {mt_b} | same as above | | | |")
            continue
        seen.add((asr_t, mt_t, mt_b))
        llm = Llama(model_path=str(hymt_path(mdir)), n_gpu_layers=0, n_ctx=2048, n_threads=mt_t,
                    n_threads_batch=mt_b, verbose=False)
        translate_all(llm, lines[:1])  # warm-up
        if a.no_asr:
            times, pairs = translate_all(llm, lines)
            asr_s = "-"
        else:
            with AsrLoad(mdir, a.whisper, asr_t, wavs) as load:
                w0 = time.perf_counter()
                times, pairs = translate_all(llm, lines)
                w1 = time.perf_counter()
            inside = load.during(w0, w1)
            del load  # free this plan's Whisper before the next plan loads its own
            asr_s = f"{statistics.median(inside):.2f}" if inside else "-"
        del llm
        out.append(f"| {name} | {asr_t} / {mt_t} / {mt_b} | {statistics.median(times):.2f} | {p90(times):.2f} "
                   f"| {scoring.chrf(pairs):.2f} | {asr_s} |")
        print(out[-1], file=sys.stderr, flush=True)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
