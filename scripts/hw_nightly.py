#!/usr/bin/env python3
"""#681: gate for the nightly hw-tests run on dev. Runs in the GitHub-hosted hw-nightly.yml, never on the self-hosted runner.

hw-tests.yml collects hardware evidence (real audio devices, GPU) on the owner's PC. Issues such as #593 want N green
runs; instead of the owner dispatching them by hand, hw-nightly.yml asks this script once a night whether hw-tests has
to run for dev's tip, and dispatches it when it does:

  - dev's tip has a green hw-tests run already  -> skip (dev has not changed since the last green run)
  - a hw-tests run for that commit is queued or running (an offline PC keeps one pending run) -> skip, never stack
  - otherwise (new commit, or the last run on it failed / was cancelled) -> run

    python scripts/hw_nightly.py --repo OWNER/REPO       writes run=true|false and sha=<dev tip> to $GITHUB_OUTPUT and
                                                         the decision (with the commit) to $GITHUB_STEP_SUMMARY
Network only through the `gh` CLI (GH_TOKEN). The decision itself is a pure function, unit-tested in
tests/test_hw_nightly_681.py.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

WORKFLOW = "hw-tests.yml"
BRANCH = "dev"
RUNS_PER_PAGE = 30  # recent runs to look at: a nightly cadence plus one per dev push
ACTIVE = ("queued", "in_progress", "waiting", "pending", "requested")  # a run that has not finished
SHORT_SHA = 7


def decide(dev_sha: str, runs: list[dict]) -> tuple[bool, str]:
    """(run, reason). `runs` = recent hw-tests runs on dev, newest first (the workflow-runs API order)."""
    short = dev_sha[:SHORT_SHA]
    for r in runs:
        if r.get("head_sha") != dev_sha:
            continue
        if r.get("status") in ACTIVE:
            return False, f"hw-tests is already {r['status']} for dev {short} (run #{r.get('id')})"
        if r.get("conclusion") == "success":
            return False, f"dev {short} unchanged since the last green hw-tests run (#{r.get('id')})"
    return True, f"dev {short} has no green or pending hw-tests run: run it"


def _gh(path: str, jq: str) -> str:
    out = subprocess.run(["gh", "api", path, "--jq", jq], capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"hw_nightly: gh api {path} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="OWNER/REPO")
    a = ap.parse_args(argv)
    if not a.repo:
        ap.error("--repo (or GITHUB_REPOSITORY) is required")
    dev_sha = _gh(f"repos/{a.repo}/commits/{BRANCH}", ".sha")
    runs = json.loads(_gh(f"repos/{a.repo}/actions/workflows/{WORKFLOW}/runs?branch={BRANCH}&per_page={RUNS_PER_PAGE}",
                          ".workflow_runs"))
    run, why = decide(dev_sha, runs)
    print(f"{why} (commit {dev_sha})")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"run={'true' if run else 'false'}\nsha={dev_sha}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"### hw-tests nightly gate\n\n{'run' if run else 'skip'}: {why}\n\ncommit `{dev_sha}`\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
