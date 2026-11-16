#!/usr/bin/env python3
"""#571: decide whether the nightly CPU eval (eval-nightly.yml) found a chrF drop and write the alert issue comment.

    python scripts/eval_nightly.py REPORT.json [--baseline eval/baseline.json] [--out alert.md] [--raise-baseline X]

A drop is a pair whose chrF x100 is below baseline chrf - chrf_margin (same rule as `tlwu_eval --gate`, chrF only: the
nightly alert is about quality, latency stays with the PR gate). Writes the markdown (pair, chrF, baseline, delta, two
decimals) to --out when there is a drop and prints `drop=true|false` (also to $GITHUB_OUTPUT when set, with
`test_run=true|false`). Exit 0 either
way; 2 on an unreadable report / baseline or a bad --raise-baseline.

--raise-baseline X (0..100, default 0) adds X to every pair's baseline chrF for this run only, so a manual
workflow_dispatch can exercise the alert path on a healthy model; the alert text then says it is a test run."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tlwu_eval import gate  # noqa: E402

TITLE = "[eval] nightly chrF drop"


def drops(report: dict, baseline: dict) -> list[dict]:
    out = []
    for row in gate.compare(report, baseline):
        b = baseline["pairs"][row["pair"]]
        if row["chrf"] is not None and b["chrf"] is not None and row["chrf"] < b["chrf"] - b["chrf_margin"]:
            out.append({**row, "margin": b["chrf_margin"]})
    return out


def raise_baseline(baseline: dict, by: float) -> dict:
    """A copy of `baseline` with `by` added to every pair's chrF (pairs without a chrF stay None)."""
    pairs = {k: {**v, "chrf": None if v["chrf"] is None else v["chrf"] + by} for k, v in baseline["pairs"].items()}
    return {**baseline, "pairs": pairs}


def parse_raise(text: str) -> float:
    try:
        v = float(text or 0)
    except ValueError:
        raise gate.GateError(f"--raise-baseline: not a number: {text!r}") from None
    if not 0 <= v <= 100:  # also rejects nan / inf
        raise gate.GateError(f"--raise-baseline: {text!r} is outside 0..100")
    return v


def alert_markdown(rows: list[dict], date: str, run_url: str = "", raised: float = 0.0) -> str:
    lines = [f"Nightly CPU eval {date}: chrF x100 below baseline minus margin.", "",
             "| pair | chrF | baseline | delta | margin |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['pair']} | {r['chrf']:.2f} | {r['base']:.2f} | {r['delta']:.2f} | {r['margin']:.2f} |")
    if raised:
        lines += ["", f"Test run: baseline chrF raised by {raised:.2f} (workflow_dispatch raise_baseline), not a real drop."]
    if run_url:
        lines += ["", f"Run: {run_url}"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("report")
    p.add_argument("--baseline", default="eval/baseline.json")
    p.add_argument("--out", default="alert.md")
    p.add_argument("--date", default=None)
    p.add_argument("--run-url", default="")
    p.add_argument("--raise-baseline", default="0", help="test only: add this to every baseline chrF (0..100)")
    a = p.parse_args(argv)
    try:
        raised = parse_raise(a.raise_baseline)
        baseline = gate.validate_baseline(gate.load_json(a.baseline, "baseline"))
        if raised:
            baseline = raise_baseline(baseline, raised)
        rows = drops(gate.load_json(a.report, "report"), baseline)
    except gate.GateError as e:
        print(f"eval_nightly: {e}", file=sys.stderr)
        return 2
    import datetime
    if rows:
        Path(a.out).write_text(alert_markdown(rows, a.date or datetime.date.today().isoformat(), a.run_url, raised), "utf-8")
    flag = "true" if rows else "false"
    print(f"drop={flag}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"drop={flag}\ntest_run={'true' if raised else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
