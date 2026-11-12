#!/usr/bin/env python3
"""Side-by-side MT table for the episode sample + self-consistency chrF. Dev tool, run in .venv-bench.

  python scripts/bench/make_table.py DIR OUT.md
DIR holds units.asr.txt, units.ref.txt and (from translate_set.py) asr.<eng>.txt / ref.<eng>.txt for each engine.
Metrics (NO human reference exists for this episode, so these are NOT quality scores):
  robustness  = chrF(system(ASR text), system(clean ref text))   how much ASR errors change that system's output
  agreement   = chrF(system A, system B) on the clean ref text, averaged over the others: a consensus proxy
"""
from __future__ import annotations

import sys
from pathlib import Path

from sacrebleu.metrics import CHRF


def read(p):
    t = Path(p).read_text("utf-8").split("\n")
    return t[:-1] if t and t[-1] == "" else t


def main() -> int:
    d, out = Path(sys.argv[1]), Path(sys.argv[2])
    engines = [e for e in ("opus", "nllb", "google") if (d / f"asr.{e}.txt").exists()]
    src_asr, src_ref = read(d / "units.asr.txt"), read(d / "units.ref.txt")
    asr = {e: read(d / f"asr.{e}.txt") for e in engines}
    ref = {e: read(d / f"ref.{e}.txt") for e in engines}
    chrf = CHRF()
    L = ["# MT comparison, private ru test set (ru -> zh-TW), %d sentences" % len(src_asr), "",
         "Contains copyrighted text: keep outside the repo.", "",
         "## Metrics (read the caveats)", "",
         "| engine | robustness chrF(asr vs clean) | agreement chrF (others, clean text) | total time s (asr set) |", "|---|---|---|---|"]
    for e in engines:
        rob = chrf.corpus_score(asr[e], [ref[e]]).score
        agr = [chrf.corpus_score(ref[e], [ref[o]]).score for o in engines if o != e]
        t = (d / f"asr.{e}.time").read_text().strip() if (d / f"asr.{e}.time").exists() else "?"
        L.append(f"| {e} | {rob:.1f} | {sum(agr) / len(agr):.1f} | {t} |")
    L += ["", "Robustness only says the output is stable under ASR noise (a bad but stable engine scores high).",
          "Agreement is a consensus proxy: engines that share errors (e.g. both pivot through English) agree without being right.",
          "Neither is a quality metric; use the FLORES chrF/COMET numbers for that.", "", "## Side by side (translation of the ASR text)", ""]
    for i, s in enumerate(src_asr):
        L += [f"**{i + 1}. RU (ASR):** {s}", ""]
        for e in engines:
            L.append(f"- `{e}`: {asr[e][i]}")
        L.append("")
    out.write_text("\n".join(L), "utf-8")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
