#!/usr/bin/env python3
"""#632: does Hy-MT2-1.8B (shipped Q4_K_M GGUF, llama.cpp CPU, default sampling) print box characters?

Dev tool (not part of the offline runtime). Runs on the macos-latest runner (`.github/workflows/eval-fffd.yml`):

  python scripts/bench/fffd_repro.py --models-dir MODELS [--flores DATA/flores200_dataset] --out OUT

Set A: the four source lines from the reporter's screenshot, each translated en -> zh (tw) with seeds 0..49.
Set B (with --flores): the first 200 FLORES-200 devtest eng_Latn lines x seeds 0..4.
For every translation it records whether the model's first (sampled) output had box characters
(tinglongwu.hymt.bad_chars: U+FFFD, private use, controls) - that is the text the pre-#632 code printed - and
whether the final printed text still has any (the #632 path: one greedy retry, then strip).
Writes OUT/fffd.jsonl (only the lines with a box before or after) and OUT/fffd.summary.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCREENSHOT = [
    "Hey ma'am.",
    "Wait, I don't be answer to Teacher! Teacher! I know the answer! Ah, yes! Great!",
    "So, uh, what do we do in school Well, the teacher asks those questions and we answer them.",
    "All right class, can anyone tell me what the capital of Idaho is?",
]


class RawTap:
    """Wraps the llama object: keeps the content of the first completion of each translate() call."""

    def __init__(self, llm):
        self.llm, self.first = llm, None

    def create_chat_completion(self, *a, **k):
        r = self.llm.create_chat_completion(*a, **k)
        if self.first is None:
            self.first = r["choices"][0]["message"]["content"] or ""
        return r


def run_set(tr, name: str, lines: list[str], seeds, bad, log=print) -> tuple[dict, list[dict]]:
    """Translate every line with every seed; count outputs with box characters before / after the #632 path."""
    from tinglongwu.engines import clean_output
    from tinglongwu.translate import strip_unk

    tap = RawTap(tr.llm)
    tr.llm = tap
    n = before = after = 0
    rows = []
    for seed in seeds:
        for i, line in enumerate(lines):
            tr.seed, tap.first = seed, None
            tr.history.clear()
            out = tr.translate(line, "en", "zh").text
            raw = strip_unk(clean_output(tap.first or ""))
            b, a = bad(raw) > 0, bad(out) > 0
            n, before, after = n + 1, before + b, after + a
            if b or a:
                rows.append({"set": name, "i": i, "seed": seed, "src": line, "raw": raw, "out": out})
        log(f"fffd_repro: {name} seed {seed}: {n} done, before={before} after={after}", flush=True)
    tr.llm = tap.llm
    return {"n": n, "before": before, "after": after}, rows


def main(argv=None, make=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--models-dir", required=True)
    ap.add_argument("--flores", default="", help="flores200_dataset dir; adds 200 eng lines x 5 seeds")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seeds", type=int, default=50, help="seeds for the screenshot lines (0..N-1)")
    ap.add_argument("--flores-lines", type=int, default=200)
    ap.add_argument("--flores-seeds", type=int, default=5)
    a = ap.parse_args(argv)
    sys.path.insert(0, str(REPO))
    from tinglongwu.hymt import HyMtTranslator, bad_chars, sampling

    tr = make(Path(a.models_dir)) if make else HyMtTranslator(Path(a.models_dir), "tw", "cpu")
    tr.preload()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res = {"sampling": sampling()}
    res["screenshot"], rows = run_set(tr, "screenshot", SCREENSHOT, range(a.seeds), bad_chars)
    if a.flores:
        lines = (Path(a.flores) / "devtest" / "eng_Latn.devtest").read_text("utf-8").splitlines()[:a.flores_lines]
        res["flores"], more = run_set(tr, "flores", lines, range(a.flores_seeds), bad_chars)
        rows += more
    res["retries"] = tr.retries
    res["reproduced"] = any(v["before"] for k, v in res.items() if isinstance(v, dict) and "before" in v)
    (out / "fffd.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    (out / "fffd.summary.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), "utf-8")
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
