"""#750: low-end CPU merge gate. Compares a `--smoke` report from the "low-end CPU" job (`--device cpu`,
TLWU_CPU_THREADS=4, default CPU models) to eval/lowend_baseline.json and fails a PR that makes the no-NVIDIA path
slower or heavier:

- per-line end-to-end (ASR + MT) p50 or p90 latency more than LATENCY_MARGIN worse than the baseline, or
- peak process RSS above RSS_MAX_MIB.

The margin and the RAM cap are owner numbers from #750 ("> 15% worse", "peak RSS > 6 GB"). GitHub's shared runners
differ by up to ~1.8x in speed between CPU types (MT median en->zh 0.93 s vs 1.69 s, #570 + #750 runs), so a fixed
number cannot carry a 15% margin. The job therefore also runs the base commit (dev tip) on the same runner and passes
it as `base_report`: that same-machine dev run is the baseline. The committed eval/lowend_baseline.json is the
fallback when the base run is missing (and the record of what a typical runner measures). A null latency there is
"not measured yet": the row is reported and only the RAM cap gates.
"""
from __future__ import annotations

import json
from pathlib import Path

from .gate import GateError, _num, load_json
from .scoring import percentile

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


def latencies(report) -> dict:
    """{latency_p50_s, latency_p90_s} of a smoke report; computed from the clips when the report is from a commit
    that did not write them yet (the base run of the first PRs after #750)."""
    if not isinstance(report, dict) or not isinstance(report.get("languages"), list):
        raise GateError("report: no 'languages' list (is this a `python -m tlwu_eval --smoke` report?)")
    lat = [c["latency_s"] for lg in report["languages"] if isinstance(lg, dict) for c in lg.get("clips", [])
           if isinstance(c, dict) and _num(c.get("latency_s"))]
    out = {}
    for k, q in zip(LAT_KEYS, (50, 90)):
        v = report.get(k)
        out[k] = v if _num(v) else (round(percentile(lat, q), 3) if lat else None)
    return out


def compare(report: dict, baseline: dict, base_report: dict | None = None) -> list[dict]:
    """Rows: metric, value, base, limit, result (PASS/FAIL/SKIP), why. base_report: the dev tip measured on the same
    machine; when given, its p50/p90 are the baseline instead of the committed numbers."""
    mine = latencies(report)
    ref = latencies(base_report) if base_report is not None else {k: baseline[k] for k in LAT_KEYS}
    rows = []
    for k in LAT_KEYS:
        v, base = mine[k], ref[k]
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


def markdown(rows: list[dict], baseline: dict, same_runner: bool = False) -> str:
    ok = not any(r["result"] == "FAIL" for r in rows)
    out = ["| metric | value | baseline | limit | result |", "|---|---|---|---|---|"]
    for r in rows:
        res = r["result"] + (f" ({r['why']})" if r["why"] else "")
        out.append(f"| {r['metric']} | {_fmt(r['value'])} | {_fmt(r['base'])} | {_fmt(r['limit'])} | {res} |")
    out += ["", f"Low-end setup: device {baseline['device']}, {baseline['threads']} threads, model {baseline['model']}, "
                f"engine {baseline['engine']}, {baseline['clips']} clips per pair. Baseline: "
                + ("the base commit (dev tip) run on this same runner." if same_runner
                   else "eval/lowend_baseline.json (no same-runner base run)."),
            f"**low-end CPU gate: {'PASS' if ok else 'FAIL'}**"]
    return "\n".join(out)


def run(report_path, baseline_path, base_report_path=None) -> tuple[int, str]:
    """-> (exit code, Markdown): 0 = within budget, 1 = regression. Raises GateError on setup errors (exit 2)."""
    baseline = validate_baseline(load_json(baseline_path, "low-end baseline"))
    base = load_json(base_report_path, "base report") if base_report_path else None
    rows = compare(load_json(report_path, "report"), baseline, base)
    return (1 if any(r["result"] == "FAIL" for r in rows) else 0), markdown(rows, baseline, base is not None)


def write_latency_jsonl(report: dict, path) -> int:
    """One JSON line per clip: {"pair", "id", "total"} (ASR + MT seconds), like tlwu's --latency-jsonl. -> lines."""
    n = 0
    with Path(path).open("w", encoding="utf-8") as f:
        for lg in report.get("languages", []):
            for c in lg.get("clips", []):
                f.write(json.dumps({"pair": lg["pair"], "id": c["id"], "total": c["latency_s"]}) + "\n")
                n += 1
    return n
