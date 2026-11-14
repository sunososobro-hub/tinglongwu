#!/usr/bin/env python3
"""#632 cold-review acceptance: which characters do the en->zh engines print that a Mac font may not draw?

Dev tool (branch fix/632-r3 only), run by .github/workflows/repro-632.yml on macos-latest:

  python repro_632.py old --src OLD_CHECKOUT --models-dir M --out OUT        # H1: OPUS/Argos PivotTranslator
  python repro_632.py dev --src DEV_CHECKOUT --models-dir M --out OUT [--flores DIR] [--seeds 50]   # Hy-MT2

Every output character outside the whitelist (ASCII 0x20-0x7E, U+00B7, U+2010-2027, U+3000-303F, U+3400-4DBF,
U+4E00-9FFF, U+FF00-FFEF) is listed with codepoint, Unicode name and category. "box-class" = the characters
the screenshot boxes can be: Co/Cn/Cs, non-BMP, U+FFFD, controls, Cyrillic/Greek/kana.
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

SCREENSHOT = [
    "Hey ma'am.",
    "Wait, I don't be answer to Teacher! Teacher! I know the answer! Ah, yes! Great!",
    "So, uh, what do we do in school Well, the teacher asks those questions and we answer them.",
    "All right class, can anyone tell me what the capital of Idaho is?",
]
WHITE = [(0x20, 0x7E), (0xB7, 0xB7), (0x2010, 0x2027), (0x3000, 0x303F), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
         (0xFF00, 0xFFEF)]


def white(ch: str) -> bool:
    c = ord(ch)
    return any(a <= c <= b for a, b in WHITE)


def box_class(ch: str) -> bool:
    c, cat = ord(ch), unicodedata.category(ch)
    return (cat in ("Co", "Cn", "Cs", "Cc") or c > 0xFFFF or c == 0xFFFD or 0x0370 <= c <= 0x04FF
            or 0x3040 <= c <= 0x30FF)


def describe(ch: str) -> dict:
    return {"char": ch, "cp": f"U+{ord(ch):04X}", "name": unicodedata.name(ch, "<no name>"),
            "cat": unicodedata.category(ch), "box_class": box_class(ch)}


def odd(text: str) -> list[dict]:
    return [describe(c) for c in text if not white(c)]


def run_old(a) -> dict:
    from tinglongwu.translate import _SENT, PivotTranslator, zh_garbage

    tr = PivotTranslator(Path(a.models_dir), "tw")
    tr.preload("en", "zh")
    step = tr._step("en", "zh")
    rows = []
    for line in SCREENSHOT:
        out = tr.translate(line, "en", "zh").text
        sents = [s for s in _SENT.split(line) if s.strip()]
        raw = [{"src": s, "opus": step(s), "odd": odd(step(s))} for s in sents]
        rows.append({"src": line, "out": out, "odd": odd(out), "zh_garbage": zh_garbage(out), "sentences": raw})
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    return {"engine": "opus (PivotTranslator, beam 4)", "screenshot": rows}


def run_dev(a) -> dict:
    from tinglongwu.hymt import HyMtTranslator, sampling

    tr = HyMtTranslator(Path(a.models_dir), "tw", "cpu")
    tr.preload()

    class Seeded:
        def __init__(self, llm):
            self.llm, self.seed = llm, 0

        def create_chat_completion(self, *x, **k):
            k["seed"] = self.seed
            return self.llm.create_chat_completion(*x, **k)

    tap = Seeded(tr.llm)
    tr.llm = tap
    res = {"engine": "hymt2-1.8b Q4_K_M CPU", "sampling": sampling()}

    def go(name, lines, seeds):
        n = with_odd = with_box = 0
        chars: Counter = Counter()
        examples = []
        for seed in seeds:
            tap.seed = seed
            for i, line in enumerate(lines):
                tr.history.clear()
                out = tr.translate(line, "en", "zh").text
                o = odd(out)
                n += 1
                with_odd += bool(o)
                with_box += any(d["box_class"] for d in o)
                for d in o:
                    chars[(d["char"], d["cp"], d["name"], d["cat"], d["box_class"])] += 1
                if o and len(examples) < 40:
                    examples.append({"i": i, "seed": seed, "src": line, "out": out})
                if name == "screenshot" and seed == 0:
                    examples.append({"i": i, "seed": 0, "src": line, "out": out, "sample": True})
            print(f"repro_632: {name} seed {seed}: n={n} with_odd={with_odd} with_box={with_box}", flush=True)
        res[name] = {"n": n, "outputs_with_out_of_whitelist": with_odd, "outputs_with_box_class": with_box,
                     "out_of_whitelist_chars": sum(chars.values()),
                     "box_class_chars": sum(v for k, v in chars.items() if k[4]),
                     "chars": [{"char": k[0], "cp": k[1], "name": k[2], "cat": k[3], "box_class": k[4], "count": v}
                               for k, v in chars.most_common()],
                     "examples": examples}

    go("screenshot", SCREENSHOT, range(a.seeds))
    if a.flores:
        lines = (Path(a.flores) / "devtest" / "eng_Latn.devtest").read_text("utf-8").splitlines()
        go("flores_devtest", lines, range(1))
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["old", "dev"])
    ap.add_argument("--src", required=True, help="checkout of the tlwu source under test")
    ap.add_argument("--models-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--flores", default="")
    ap.add_argument("--seeds", type=int, default=50)
    a = ap.parse_args()
    sys.path.insert(0, str(Path(a.src).resolve()))
    res = run_old(a) if a.mode == "old" else run_dev(a)
    res["unicodedata"] = unicodedata.unidata_version
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"repro_632_{a.mode}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
