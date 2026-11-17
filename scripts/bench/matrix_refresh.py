#!/usr/bin/env python3
"""#1352: refresh the 6x6 traffic-light matrix in docs/benchmarks.md once per published release (never a release gate).

eval-matrix-release.yml runs this once a day on a GitHub-hosted runner. For the latest published release tag it does
one step at a time, so a day without a new release costs one API call and nothing else:

  - a refresh issue for that tag exists already            -> done
  - an eval-flores run on the tag is queued / running      -> wait
  - a finished green run on the tag has `flores-6x6-table` -> open the refresh issue with that table and the run link;
                                                               a worker then updates docs/benchmarks.md via a fix/N PR
  - MAX_ATTEMPTS runs on the tag failed                    -> open the refresh issue saying the run failed (no table)
  - otherwise                                               -> dispatch `eval-flores.yml --ref <tag> -f matrix=6x6`

release.yml is not touched: the run starts from the published tag, after the release.

    python scripts/bench/matrix_refresh.py --repo OWNER/REPO   decide, then dispatch or open the issue (gh CLI, GH_TOKEN)
    python scripts/bench/matrix_refresh.py --repo OWNER/REPO --dry-run   decide and print only

The decision is a pure function (`decide`), unit-tested in tests/test_matrix_refresh_1352.py. Network only through `gh`.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKFLOW = "eval-flores.yml"
TABLE_ARTIFACT = "flores-6x6-table"
ACTIVE = ("queued", "in_progress", "waiting", "pending", "requested")
MAX_ATTEMPTS = 2  # failed 6x6 runs on one tag before the issue reports the failure instead of dispatching again
TITLE = "[eval] refresh the 6x6 matrix in docs/benchmarks.md for {tag}"
LABELS = ["src:ai", "ok-to-work", "status:todo", "P2", "documentation", "model:sonnet", "area:eval", "mode:listen"]


def title(tag: str) -> str:
    return TITLE.format(tag=tag)


def decide(tag: str, runs: list[dict], artifacts: dict, issue_exists: bool) -> tuple[str, dict | None, str]:
    """(action, run, reason); action in done | wait | issue | failed | dispatch.

    runs = eval-flores runs whose head_branch is the tag, newest first; artifacts = {run id: [artifact names]} for the
    finished green runs (only those are looked up)."""
    if issue_exists:
        return "done", None, f"refresh issue for {tag} exists"
    mine = [r for r in runs if r.get("head_branch") == tag]
    for r in mine:
        if r.get("status") in ACTIVE:
            return "wait", r, f"eval-flores run {r.get('id')} on {tag} is {r['status']}"
    for r in mine:
        if r.get("conclusion") == "success" and TABLE_ARTIFACT in artifacts.get(r.get("id"), []):
            return "issue", r, f"eval-flores run {r.get('id')} on {tag} has the 6x6 table"
    failed = [r for r in mine if r.get("status") == "completed" and r.get("conclusion") != "success"]
    if len(failed) >= MAX_ATTEMPTS:
        return "failed", failed[0], f"{len(failed)} eval-flores runs on {tag} failed; reporting instead of retrying"
    return "dispatch", None, f"no 6x6 matrix run on {tag} yet: dispatch it"


def issue_body(tag: str, run: dict, table: str | None) -> str:
    url = run.get("html_url", "")
    rid = run.get("id")
    if table is None:
        head = (f"Why: #1352 refreshes the 6x6 traffic-light matrix once per release; the `matrix=6x6` eval-flores runs "
                f"on {tag} failed ({MAX_ATTEMPTS} attempts, last: {url}).\n\n"
                f"Goal: Find why the 6x6 matrix run fails on {tag}, fix it on dev, and rerun it on the tag.\n\n"
                f"Acceptance: `gh workflow run eval-flores.yml --ref {tag} -f matrix=6x6` ends green with a "
                f"`{TABLE_ARTIFACT}` artifact, and docs/benchmarks.md carries its traffic-light table.\n\n")
    else:
        head = (f"Why: #1352 (owner: keep the traffic-light picture in the docs and refresh it per release). "
                f"The `matrix=6x6` eval-flores run on {tag} finished: {url}\n\n"
                f"Goal: docs/benchmarks.md shows the 6x6 traffic-light table of run {rid} ({tag}).\n\n"
                f"Acceptance: the \"Traffic lights\" table and its counts line in docs/benchmarks.md match the table "
                f"below; `scripts/bench/data/matrix_6x6_{rid}.json` is committed and the docs command names it; "
                f"the Results line names run {rid} and {tag}.\n\n")
    verify = (f"Verify: `python scripts/bench/matrix_lights.py scripts/bench/data/matrix_6x6_{rid}.json` prints the "
              f"table in docs/benchmarks.md (summaries: `gh run download {rid} -p 'flores-*'`).\n\n"
              f"Out of scope: changing the thresholds, the model or release gates.\n")
    tail = f"\nTable from run {rid}:\n\n{table}\n" if table else ""
    return head + verify + tail


def _gh(*args: str, check: bool = True) -> str:
    out = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and out.returncode:
        sys.exit(f"matrix_refresh: gh {' '.join(args[:3])} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def _issue_exists(repo: str, tag: str) -> bool:
    q = f'repo:{repo} is:issue in:title "{title(tag)}"'
    found = json.loads(_gh("api", "-X", "GET", "search/issues", "-f", f"q={q}", "--jq", "[.items[].title]") or "[]")
    return title(tag) in found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="OWNER/REPO")
    ap.add_argument("--dry-run", action="store_true", help="decide and print, change nothing")
    a = ap.parse_args(argv)
    if not a.repo:
        ap.error("--repo (or GITHUB_REPOSITORY) is required")
    tag = _gh("api", f"repos/{a.repo}/releases/latest", "--jq", ".tag_name")
    runs = json.loads(_gh("api", f"repos/{a.repo}/actions/workflows/{WORKFLOW}/runs?branch={tag}&per_page=30",
                          "--jq", ".workflow_runs") or "[]")
    arts = {r["id"]: json.loads(_gh("api", f"repos/{a.repo}/actions/runs/{r['id']}/artifacts", "--jq",
                                    "[.artifacts[].name]") or "[]")
            for r in runs if r.get("conclusion") == "success"}
    action, run, why = decide(tag, runs, arts, _issue_exists(a.repo, tag))
    print(f"{action}: {why}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"### 6x6 matrix refresh (#1352)\n\n{action}: {why}\n")
    if a.dry_run or action in ("done", "wait"):
        return 0
    if action == "dispatch":
        _gh("workflow", "run", WORKFLOW, "--repo", a.repo, "--ref", tag, "-f", "matrix=6x6")
        return 0
    table = None
    if action == "issue":
        with tempfile.TemporaryDirectory() as d:
            _gh("run", "download", str(run["id"]), "--repo", a.repo, "-n", TABLE_ARTIFACT, "-D", d)
            table = (Path(d) / "table.md").read_text("utf-8").strip()
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(issue_body(tag, run, table))
    _gh("issue", "create", "--repo", a.repo, "--title", title(tag), "--body-file", f.name,
        *[x for lab in LABELS for x in ("--label", lab)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
