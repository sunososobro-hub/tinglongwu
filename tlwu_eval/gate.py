"""#361 (#340 part 1): merge gate - compare a `--smoke` report to the committed baseline (eval/baseline.json).
Per pair, FAIL when chrF < chrf - chrf_margin or MT median > mt_median_s_max; no clips = SKIP. Every number comes
from the baseline file, none is a constant here; a null `chrf` (not measured yet) gates that pair on latency only."""
from __future__ import annotations

import json
import math
from pathlib import Path

PAIR_KEYS = ("chrf", "chrf_margin", "mt_median_s_max")
META_KEYS = ("model", "engine", "clips")


class GateError(ValueError):
    """Unreadable or invalid baseline / report: a setup error (exit 2), not a regression."""


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate_baseline(b) -> dict:
    """Check the baseline schema; returns it unchanged or raises GateError naming the bad field."""
    if not isinstance(b, dict):
        raise GateError("baseline: top level must be a JSON object")
    for k in (k for k in META_KEYS if k not in b):
        raise GateError(f"baseline: missing '{k}' (what the numbers were measured with)")
    if not isinstance(b["clips"], int) or isinstance(b["clips"], bool) or b["clips"] < 1:
        raise GateError("baseline: 'clips' must be a positive integer")
    pairs = b.get("pairs")
    if not isinstance(pairs, dict) or not pairs:
        raise GateError("baseline: 'pairs' must be a non-empty object like {\"en->zh\": {...}}")
    for pair, row in pairs.items():
        if not isinstance(row, dict):
            raise GateError(f"baseline: {pair} must be an object")
        for k in (k for k in PAIR_KEYS if k not in row):
            raise GateError(f"baseline: {pair} is missing '{k}'")
        if not _num(row["mt_median_s_max"]) or row["mt_median_s_max"] <= 0:
            raise GateError(f"baseline: {pair}.mt_median_s_max must be a number > 0")
        if (row["chrf"] is None) != (row["chrf_margin"] is None):
            raise GateError(f"baseline: {pair}.chrf and chrf_margin must both be numbers or both be null")
        if row["chrf"] is not None and not (_num(row["chrf"]) and 0 <= row["chrf"] <= 100):
            raise GateError(f"baseline: {pair}.chrf must be a number in 0..100 (chrF x100)")
        if row["chrf"] is not None and not (_num(row["chrf_margin"]) and row["chrf_margin"] >= 0):
            raise GateError(f"baseline: {pair}.chrf_margin must be a number >= 0")
    return b


def load_json(path, what: str):
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except FileNotFoundError:
        raise GateError(f"{what} not found: {path}") from None
    except (OSError, ValueError) as e:
        raise GateError(f"{what} {path} is not valid JSON: {e}") from None


def compare(report: dict, baseline: dict) -> list[dict]:
    """One row per baseline pair: pair, chrf, base, delta, median, budget, result (PASS/FAIL/SKIP), why."""
    langs = report.get("languages") if isinstance(report, dict) else None
    if not isinstance(langs, list):
        raise GateError("report: no 'languages' list (is this a `python -m tlwu_eval --smoke` report?)")
    by_pair = {}
    for lg in (lg for lg in langs if isinstance(lg, dict)):
        if lg.get("pair") in by_pair:
            raise GateError(f"report: pair {lg.get('pair')} is listed more than once")
        by_pair[lg.get("pair")] = lg
    rows = []
    for pair, b in baseline["pairs"].items():
        lg = by_pair.get(pair)
        row = {"pair": pair, "chrf": None, "base": b["chrf"], "delta": None, "median": None,
               "budget": b["mt_median_s_max"], "result": "SKIP", "why": "no clips in the report"}
        if lg is None or not lg.get("clips"):
            rows.append(row)
            continue
        why = []
        row["chrf"], row["median"] = (v if _num(v) else None for v in (lg.get("chrf"), lg.get("mt_median_s")))
        if b["chrf"] is not None:
            if row["chrf"] is None:
                why.append("no chrF in the report")
            else:
                row["delta"] = row["chrf"] - b["chrf"]
                if row["chrf"] < b["chrf"] - b["chrf_margin"]:
                    why.append(f"chrF dropped more than {b['chrf_margin']:.2f}")
        if row["median"] is None:
            why.append("no MT median in the report")
        elif row["median"] > b["mt_median_s_max"]:
            why.append(f"MT median over {b['mt_median_s_max']:g}s")
        row["result"], row["why"] = ("FAIL" if why else "PASS"), "; ".join(why)
        rows.append(row)
    return rows


def _fmt(v, spec: str) -> str:
    return "n/a" if v is None else format(v, spec)


def _verdict(rows: list[dict]) -> tuple[bool, str]:
    """(ok, note). Nothing evaluated (every row SKIP) is a FAIL: a broken data checkout must not go green."""
    if any(r["result"] == "FAIL" for r in rows):
        return False, ""
    if not any(r["result"] == "PASS" for r in rows):
        return False, " (no pair evaluated)"
    return True, ""


def markdown(rows: list[dict], baseline: dict) -> str:
    """Markdown table for $GITHUB_STEP_SUMMARY: chrF x100 to two decimals, delta, MT median, PASS/FAIL/SKIP."""
    out = ["| pair | chrF | baseline | delta | MT median s | budget s | result |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        res = r["result"] + (f" ({r['why']})" if r["why"] and r["result"] != "PASS" else "")
        out.append(f"| {r['pair']} | {_fmt(r['chrf'], '.2f')} | {_fmt(r['base'], '.2f')} | {_fmt(r['delta'], '+.2f')} | "
                   f"{_fmt(r['median'], '.3f')} | {r['budget']:.2f} | {res} |")
    ok, note = _verdict(rows)
    out += ["", f"Baseline measured with: model {baseline['model']}, engine {baseline['engine']}, "
                f"{baseline['clips']} clips per pair.", f"**eval gate: {'PASS' if ok else 'FAIL'}**{note}"]
    return "\n".join(out)


def run(report_path, baseline_path) -> tuple[int, str]:
    """-> (exit code, Markdown). 0 = no regression, 1 = a pair FAILed or no pair was evaluated. Raises GateError on setup errors."""
    baseline = validate_baseline(load_json(baseline_path, "baseline"))
    rows = compare(load_json(report_path, "report"), baseline)
    return (0 if _verdict(rows)[0] else 1), markdown(rows, baseline)
