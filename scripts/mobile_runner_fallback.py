#!/usr/bin/env python3
"""mobile-apk.yml helper (#1823): fall back to ubuntu when the home runner does not pick the build up.

GitHub cannot move a queued job to another runner, so with MOBILE_RUNNER=tlwu-mobile the `runner-fallback` job
(ubuntu, beside `build`) watches this run's `build` job. If no runner has taken it after WAIT_MIN minutes (home PC
off, or busy with another job), it dispatches mobile-apk.yml again on the same branch with `runner=ubuntu` (plus the
original event, so the Play-upload plan and publish behave as before) and then cancels this run. The new run has the
same head SHA and is newer, so the merge gate (scripts/merge_gate.py reads the newest mobile-apk run) uses it.
If `build` starts in time, nothing happens.

    python3 -I scripts/mobile_runner_fallback.py watch
      env: GH_TOKEN, REPO, RUN_ID, REF (branch name), EVENT (original event), SOURCE_SHA (github.sha of this run),
           X86 (true/false), WAIT_MIN (default 5)

Pure functions are unit-tested (tests/test_mobile_runner_fallback_1823.py); network only through `gh api`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

WAIT_MIN = 5          # owner #1621: phone previews within minutes; the issue (#1823) names 5 min
POLL_S = 30
BUILD_JOB = "build"
WORKFLOW = "mobile-apk.yml"


def picked_up(jobs: list[dict], name: str = BUILD_JOB) -> bool:
    """True once a runner has taken the build job (or it already ended). Missing job = not picked up."""
    for j in jobs:
        if j.get("name") != name:
            continue
        if j.get("runner_name") or j.get("status") in ("in_progress", "completed"):
            return True
    return False


def dispatch_body(ref: str, event: str, x86: bool, waited_min: int, sha: str = "") -> dict:
    """`ref` only selects the branch to run on; `source_sha` is the revision the cancelled run built (for a PR the
    merge commit, not the head branch), so the replacement builds and publishes exactly the same tree."""
    return {"ref": ref, "inputs": {"runner": "ubuntu", "fallback_after": str(waited_min),
                                   "fallback_event": event, "source_sha": sha, "x86_64_debug": bool(x86)}}


def watch(get_jobs, wait_min: float = WAIT_MIN, poll_s: float = POLL_S,
          clock=time.monotonic, sleep=time.sleep) -> tuple[bool, int]:
    """Poll until the build is picked up or wait_min passes. Returns (picked_up, minutes waited)."""
    start = clock()
    while True:
        if picked_up(get_jobs()):
            return True, int((clock() - start) // 60)
        waited = clock() - start
        if waited >= wait_min * 60:
            return False, int(round(waited / 60))
        sleep(min(poll_s, wait_min * 60 - waited))


def _gh(args: list[str], body: dict | None = None) -> str:
    r = subprocess.run(["gh", "api", *args] + (["--input", "-"] if body is not None else []),
                       input=json.dumps(body) if body is not None else "",
                       capture_output=True, text=True, check=True)
    return r.stdout


def main(argv: list[str]) -> int:
    if argv[1:] != ["watch"]:
        print(__doc__, file=sys.stderr)
        return 2
    repo, run_id, ref = os.environ["REPO"], os.environ["RUN_ID"], os.environ["REF"]
    event = os.environ.get("EVENT", "push")
    x86 = os.environ.get("X86", "false") == "true"
    wait_min = float(os.environ.get("WAIT_MIN") or WAIT_MIN)

    def jobs():
        return json.loads(_gh([f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"])).get("jobs", [])

    ok, waited = watch(jobs, wait_min)
    if ok:
        print(f"home runner picked up the build after {waited} min; no fallback")
        return 0
    print(f"runner fallback: ubuntu after {waited} min (dispatching {WORKFLOW} on {ref}, cancelling run {run_id})")
    _gh(["-X", "POST", f"repos/{repo}/actions/workflows/{WORKFLOW}/dispatches"],
        dispatch_body(ref, event, x86, waited, os.environ.get("SOURCE_SHA", "")))
    _gh(["-X", "POST", f"repos/{repo}/actions/runs/{run_id}/cancel"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
