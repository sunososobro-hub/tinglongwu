"""CI cost per merged dev PR (#1920).

For each PR: every `pull_request` workflow run on its head branch created between PR open and merge,
the job-minutes of all their jobs, and the wall time of the `ci` run on the merged head.
Prints one line per PR and a summary (median `ci` minutes, median / mean job-minutes, PRs with >= 2 `ci` runs).

Usage: python scripts/ci_pr_cost.py --repo OWNER/NAME PR [PR ...]
Reads through `gh api` (REST only). Read-only: never writes to GitHub.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
from datetime import datetime


def _ts(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def _minutes(start: str | None, end: str | None) -> float:
    a, b = _ts(start), _ts(end)
    return max((b - a).total_seconds() / 60, 0.0) if a and b else 0.0


def pr_cost(pr: dict, runs: list[dict], jobs_by_run: dict[int, list[dict]]) -> dict:
    """Cost of one PR from its REST pull, its branch's workflow runs and each run's jobs."""
    opened, merged = _ts(pr["created_at"]), _ts(pr["merged_at"])
    own = [
        r for r in runs
        if r.get("event") == "pull_request" and opened <= _ts(r["created_at"]) <= merged
    ]
    job_min = sum(_minutes(j.get("started_at"), j.get("completed_at")) for r in own for j in jobs_by_run.get(r["id"], []))
    ci_runs = [r for r in own if r.get("name") == "ci"]
    head = [r for r in ci_runs if r.get("head_sha") == pr["head"]["sha"]]
    ci_min = max((_minutes(r.get("run_started_at"), r.get("updated_at")) for r in head), default=None)
    return {"number": pr["number"], "ci_min": ci_min, "job_min": job_min, "ci_runs": len(ci_runs)}


def summarize(costs: list[dict]) -> dict:
    ci = [c["ci_min"] for c in costs if c["ci_min"] is not None]
    jm = [c["job_min"] for c in costs]
    return {
        "prs": len(costs),
        "ci_median": round(statistics.median(ci), 1) if ci else None,
        "job_median": round(statistics.median(jm), 1) if jm else None,
        "job_mean": round(statistics.mean(jm), 1) if jm else None,
        "multi_ci": sum(1 for c in costs if c["ci_runs"] >= 2),
        "max_ci_runs": max((c["ci_runs"] for c in costs), default=0),
    }


def _gh(path: str) -> dict:
    return json.loads(subprocess.run(["gh", "api", path], check=True, capture_output=True, text=True).stdout)


def fetch(repo: str, number: int) -> dict:
    pr = _gh(f"repos/{repo}/pulls/{number}")
    runs: list[dict] = []
    page = 1
    while True:
        got = _gh(f"repos/{repo}/actions/runs?branch={pr['head']['ref']}&event=pull_request&per_page=100&page={page}")
        runs += got["workflow_runs"]
        if len(got["workflow_runs"]) < 100:
            break
        page += 1
    jobs = {r["id"]: _gh(f"repos/{repo}/actions/runs/{r['id']}/jobs?filter=all&per_page=100")["jobs"] for r in runs}
    return pr_cost(pr, runs, jobs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("prs", nargs="+", type=int)
    args = ap.parse_args(argv)
    costs = [fetch(args.repo, n) for n in args.prs]
    for c in costs:
        ci = "-" if c["ci_min"] is None else f"{c['ci_min']:.1f}"
        print(f"#{c['number']} ci_min={ci} job_min={c['job_min']:.1f} ci_runs={c['ci_runs']}")
    print(" ".join(f"{k}={v}" for k, v in summarize(costs).items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
