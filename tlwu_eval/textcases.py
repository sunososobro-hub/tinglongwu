"""#827: MT-only pass over the text cases in eval/user_cases.jsonl (real translation errors, #679).

`python -m tlwu_eval --text-cases eval/user_cases.jsonl [--out report.json]` translates each case's `src` with the
configured MT engine and reports chrF per pair and per case, plus whether the engine still gives the reported output.
Informational only: it is not a red/green gate."""
from __future__ import annotations

import json
from collections import OrderedDict

from . import scoring

REQUIRED = ("id", "pair", "src", "ref")


def load_cases(path) -> list[dict]:
    cases = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as e:
                raise ValueError(f"{path}:{n}: not valid JSON ({e})") from e
            missing = [k for k in REQUIRED if not isinstance(row.get(k), str) or not row[k].strip()]
            if missing:
                raise ValueError(f"{path}:{n}: missing {', '.join(missing)}")
            if "->" not in row["pair"]:
                raise ValueError(f"{path}:{n}: pair {row['pair']!r} is not 'src->tgt'")
            cases.append(row)
    return cases


def run(cases, mt) -> dict:
    """mt.translate(text, src, tgt) -> object with .text. Returns {'pairs': {pair: {n, chrf}}, 'cases': [rows]}."""
    rows, by_pair = [], OrderedDict()
    for c in cases:
        src, tgt = (x.strip() for x in c["pair"].split("->", 1))
        out = mt.translate(c["src"], src, tgt).text.strip()
        rows.append({"id": c["id"], "pair": c["pair"], "chrf": round(scoring.chrf([(c["ref"], out)]), 1),
                     "same_as_reported": bool(c.get("tlwu")) and out == c["tlwu"].strip(), "output": out})
        by_pair.setdefault(c["pair"], []).append((c["ref"], out))
    pairs = {p: {"n": len(v), "chrf": round(scoring.chrf(v), 1)} for p, v in by_pair.items()}
    return {"pairs": pairs, "cases": rows}


def format_report(rep: dict) -> str:
    lines = [f"{p}: {e['n']} cases, chrF {e['chrf']}" for p, e in rep["pairs"].items()]
    lines += [f"  {r['id']}: chrF {r['chrf']} same_as_reported {str(r['same_as_reported']).lower()}" for r in rep["cases"]]
    return "\n".join(lines)
