#!/usr/bin/env python3
"""#1352: 6x6 language-pair matrix as a traffic-light Markdown table with the Google reference and the gap per cell.

  python scripts/bench/matrix_lights.py RESULTS [--google scripts/bench/data/google_refs.json]

RESULTS = a JSON file ({"summaries": [...]} or a list) or a directory of `*.summary.json` (the eval-flores `arts` dir).
Dev tool; the product never imports scripts/. No network.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MATRIX6 = ["eng_Latn", "rus_Cyrl", "zho_Hans", "zho_Hant", "jpn_Jpan", "kor_Hang"]
NAMES = {"eng_Latn": "en", "rus_Cyrl": "ru", "zho_Hans": "zh-Hans", "zho_Hant": "zh-Hant", "jpn_Jpan": "ja",
         "kor_Hang": "ko"}
GREEN_MIN = 0.880   # COMET (3 decimals) >= this: green
YELLOW_MIN = 0.870  # 0.870-0.879: yellow; below: red (0.870 = weakest shipped direction when #1305 ran)
ICON = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
SCRIPT_PAIRS = {("zho_Hans", "zho_Hant"), ("zho_Hant", "zho_Hans")}  # OpenCC conversion, not MT
DEFAULT_GOOGLE = Path(__file__).resolve().parent / "data" / "google_refs.json"


def light(comet: float) -> str:
    c = round(float(comet), 3)
    return "green" if c >= GREEN_MIN else "yellow" if c >= YELLOW_MIN else "red"


def load_summaries(path: str) -> list[dict]:
    p = Path(path)
    if p.is_dir():
        return [json.loads(f.read_text("utf-8")) for f in sorted(p.rglob("*.summary.json"))]
    d = json.loads(p.read_text("utf-8"))
    return d["summaries"] if isinstance(d, dict) else d


def load_google(path) -> dict:
    p = Path(path) if path else DEFAULT_GOOGLE
    return json.loads(p.read_text("utf-8")).get("refs", {}) if p.exists() else {}


def render(summaries: list[dict], google: dict | None = None) -> str:
    google = google or {}
    cell = {}
    for s in summaries:
        a, _, b = str(s.get("pair", "")).partition("->")
        if a in MATRIX6 and b in MATRIX6 and a != b and s.get("COMET") is not None:
            cell[(a, b)] = s
    rows = ["| source \\ target | " + " | ".join(NAMES[t] for t in MATRIX6) + " |", "|---" * 7 + "|"]
    count = {"green": 0, "yellow": 0, "red": 0}
    worst = None  # (gap, a, b)
    for a in MATRIX6:
        out = []
        for b in MATRIX6:
            s = cell.get((a, b))
            if a == b:
                out.append("")
                continue
            if s is None:
                out.append("-")
                continue
            lt = light(s["COMET"])
            count[lt] += 1
            txt = f"{ICON[lt]} {s['COMET']:.3f}"
            if (a, b) in SCRIPT_PAIRS:
                txt += " (script conversion)"
            g = google.get(f"{a}->{b}")
            if g:
                gap = round(g["COMET"] - s["COMET"], 3)
                txt += f"<br>G {g['COMET']:.3f} (gap {gap:+.3f})"
                if worst is None or gap > worst[0]:
                    worst = (gap, a, b)
            else:
                txt += "<br>G -"
            out.append(txt)
        rows.append(f"| **{NAMES[a]}** | " + " | ".join(out) + " |")
    foot = f"\n{count['green']} green / {count['yellow']} yellow / {count['red']} red " \
           f"(green >= {GREEN_MIN:.3f}, yellow {YELLOW_MIN:.3f}-{GREEN_MIN - 0.001:.3f}, red < {YELLOW_MIN:.3f}); "
    foot += (f"largest gap to Google: {NAMES[worst[1]]} -> {NAMES[worst[2]]} {worst[0]:+.3f}"
             if worst else "no Google reference in this run")
    return "\n".join(rows) + "\n" + foot + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("results")
    ap.add_argument("--google", default=None)
    a = ap.parse_args(argv)
    sys.stdout.write(render(load_summaries(a.results), load_google(a.google)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
