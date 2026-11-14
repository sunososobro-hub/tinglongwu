"""#750: low-end CPU merge gate. Compares a `--smoke` report from the "low-end CPU" job (`--device cpu`,
TLWU_CPU_THREADS=4, default CPU models) to eval/lowend_baseline.json and fails a PR that makes the no-NVIDIA path
slower or heavier:

- per-line end-to-end (ASR + MT) p50 or p90 latency more than LATENCY_MARGIN worse than the baseline, or
- peak process RSS above RSS_MAX_MIB.

The margin and the RAM cap are owner numbers from #750 ("> 15% worse", "peak RSS > 6 GB"); the baseline latencies
are measured on the CI runner and live in the repo file. A null baseline latency is "not measured yet": that row
is reported (so the numbers to commit are in the job summary) and only the RAM cap gates.
"""
from __future__ import annotations

import json
from pathlib import Path

from .gate import GateError, _num, load_json

LATENCY_MARGIN = 0.15  # owner, #750: fail when p50 or p90 is more than 15% worse than the dev baseline
RSS_MAX_MIB = 6 * 1024  # owner, #750: fail when peak RSS > 6 GB (a 16 GB laptop also runs a browser + meeting app)
LAT_KEYS = ("latency_p50_s", "latency_p90_s")
META_KEYS = ("model", "engine", "device", "threads", "clips")


def validate_baseline(b) -> dict:
    if not isinstance(b, dict):
        raise GateError("low-end baseline: top level must be a JSON object")
    for k in META_KEYS + LAT_KEYS:
        if k not in b:
            raise GateError(f"low-end baseline: missing '{k}'")
    for k in LAT_KEYS:
        if b[k] is not None and not (_num(b[k]) and b[k] > 0):
            raise GateError(f"low-end baseline: {k} must be a number > 0 (seconds) or null (not measured yet)")
    return b


def compare(report: dict, baseline: dict) -> list[dict]:
    """Rows: metric, value, base, limit, result (PASS/FAIL/SKIP), why."""
    if not isinstance(report, dict) or not isinstance(report.get("languages"), list):
        raise GateError("report: no 'languages' list (is this a `python -m tlwu_eval --smoke` report?)")
    rows = []
    for k in LAT_KEYS:
        v, base = report.get(k), baseline[k]
        row = {"metric": k, "value": v if _num(v) else None, "base": base, "limit": None, "result": "PASS", "why": ""}
        if row["value"] is None:
            row["result"], row["why"] = "FAIL", "not in the report"
        elif base is None:
            row["result"], row["why"] = "SKIP", "no baseline yet: commit this value to eval/lowend_baseline.json"
        else:
            row["limit"] = round(base * (1 + LATENCY_MARGIN), 3)
            if row["value"] > row["limit"]:
                worse = 100 * (row["value"] / base - 1)
                row["result"], row["why"] = "FAIL", f"{worse:.0f}% worse than the baseline (max {100 * LATENCY_MARGIN:.0f}%)"
        rows.append(row)
    rss = report.get("peak_rss_mib")
    if not _num(rss):
        rss = report.get("peak_ram_mib")  # no `resource` module (Windows): the sampled peak is the best there is
    row = {"metric": "peak_rss_mib", "value": rss if _num(rss) else None, "base": None, "limit": RSS_MAX_MIB,
           "result": "PASS", "why": ""}
    if row["value"] is None:
        row["result"], row["why"] = "FAIL", "not in the report"
    elif row["value"] > RSS_MAX_MIB:
        row["result"], row["why"] = "FAIL", f"over the {RSS_MAX_MIB} MiB cap"
    rows.append(row)
    return rows


def _fmt(v) -> str:
    return "n/a" if v is None else f"{v:g}"


def markdown(rows: list[dict], baseline: dict) -> str:
    ok = not any(r["result"] == "FAIL" for r in rows)
    out = ["| metric | value | baseline | limit | result |", "|---|---|---|---|---|"]
    for r in rows:
        res = r["result"] + (f" ({r['why']})" if r["why"] else "")
        out.append(f"| {r['metric']} | {_fmt(r['value'])} | {_fmt(r['base'])} | {_fmt(r['limit'])} | {res} |")
    out += ["", f"Low-end setup: device {baseline['device']}, {baseline['threads']} threads, model {baseline['model']}, "
                f"engine {baseline['engine']}, {baseline['clips']} clips per pair.",
            f"**low-end CPU gate: {'PASS' if ok else 'FAIL'}**"]
    return "\n".join(out)


def run(report_path, baseline_path) -> tuple[int, str]:
    """-> (exit code, Markdown): 0 = within budget, 1 = regression. Raises GateError on setup errors (exit 2)."""
    baseline = validate_baseline(load_json(baseline_path, "low-end baseline"))
    rows = compare(load_json(report_path, "report"), baseline)
    return (1 if any(r["result"] == "FAIL" for r in rows) else 0), markdown(rows, baseline)


def write_latency_jsonl(report: dict, path) -> int:
    """One JSON line per clip: {"pair", "id", "total"} (ASR + MT seconds), like tlwu's --latency-jsonl. -> lines."""
    n = 0
    with Path(path).open("w", encoding="utf-8") as f:
        for lg in report.get("languages", []):
            for c in lg.get("clips", []):
                f.write(json.dumps({"pair": lg["pair"], "id": c["id"], "total": c["latency_s"]}) + "\n")
                n += 1
    return n
