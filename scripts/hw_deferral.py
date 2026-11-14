#!/usr/bin/env python3
"""#848: releases must not stall when the owner's self-hosted hw-tests runner (tlwu-hw) is offline.

Owner decision 2026-11-14 ("不然我家停電不就Gg"): if the hw-tests run for the release PR head has not STARTED (no
runner picked its job up) within HW_RUNNER_WAIT_MIN minutes, hw-tests is deferred: auto-release merges on ci, e2e and
windows-build alone, the release notes say "hardware checks deferred", and hw-tests runs later when the PC is back.
If that later hw-tests run (on the released commit or a newer dev push, before any hw-tests success) fails, the
`followup` command opens a P0 issue "[release] deferred hw-tests failed on vX.Y.Z".
A started hw-tests run is never deferred: online behaviour is unchanged (success still required).

    python scripts/hw_deferral.py decide --runs RUNS.json [--jobs JOBS.json] [--now ISO]   dry run of the gate
    python scripts/hw_deferral.py followup                                 auto-release.yml after hw-tests failed

Pure functions are unit-tested (tests/test_hw_deferral.py); network only through the `gh` CLI (GH_TOKEN).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

HW_RUNNER_WAIT_MIN = 30          # minutes the release waits for the tlwu-hw runner to pick hw-tests up
HW_CHECK = "hw-tests"
CLOUD_CHECKS = ("ci", "e2e", "windows-build", "audio-routing")   # what a deferred release still needs green (#849: audio-routing)
DEFERRED_LINE = "hardware checks deferred"
DEFERRED_MARKER = "<!-- tlwu:hw-deferred -->"   # auto-release adds it to the release PR body before merging
FAILED_TITLE = "[release] deferred hw-tests failed on {tag}"
ISSUE_LABELS = ("P0", "bug", "area:ci", "mode:shared", "model:opus", "status:todo", "ok-to-work")


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def newest_hw_run(runs: list[dict]) -> dict | None:
    mine = [r for r in runs if r.get("name") == HW_CHECK]
    return max(mine, key=lambda r: r.get("id", 0)) if mine else None


def hw_started(run: dict, jobs: list[dict]) -> bool:
    """True once a runner took the hw job. A queued / pending run, or one cancelled while still waiting for a
    runner, has not started. A completed failure we cannot see jobs for counts as started (never ship over it)."""
    if run.get("status") == "in_progress":
        return True
    if any(j.get("runner_name") or j.get("status") == "in_progress" or j.get("steps") for j in jobs):
        return True
    return run.get("status") == "completed" and run.get("conclusion") != "cancelled" and not jobs


def decide(runs: list[dict], jobs_of=lambda run_id: [], now: datetime | None = None) -> dict:
    """The hw-tests part of the release gate for one commit.
    state: green (success), started (running or failed: not green, never deferred), missing (no run),
    waiting (queued for < HW_RUNNER_WAIT_MIN or no timestamp), deferred (queued for >= HW_RUNNER_WAIT_MIN)."""
    now = now or datetime.now(timezone.utc)
    hw = newest_hw_run(runs)
    if hw is None:
        return {"state": "missing", "reason": f"{HW_CHECK}: no run on this commit"}
    if hw.get("status") == "completed" and hw.get("conclusion") == "success":
        return {"state": "green", "reason": f"{HW_CHECK}: success"}
    if hw_started(hw, jobs_of(hw.get("id"))):
        return {"state": "started", "reason": f"{HW_CHECK}: started, {hw.get('status')}/{hw.get('conclusion')}"}
    created = _ts(hw.get("created_at"))
    if created is None:
        return {"state": "waiting", "reason": f"{HW_CHECK}: not started, no created_at to time the wait"}
    waited = (now - created).total_seconds() / 60
    if waited < HW_RUNNER_WAIT_MIN:
        return {"state": "waiting",
                "reason": f"{HW_CHECK}: not started after {waited:.0f} min (deferred at {HW_RUNNER_WAIT_MIN})"}
    return {"state": "deferred",
            "reason": f"{HW_CHECK}: not started after {waited:.0f} min >= {HW_RUNNER_WAIT_MIN}: {DEFERRED_LINE}"}


def required_checks(state: str, all_checks: tuple[str, ...]) -> tuple[str, ...]:
    """The checks the release PR head must pass: hw-tests drops out only when it is deferred."""
    return tuple(c for c in all_checks if c != HW_CHECK) if state == "deferred" else tuple(all_checks)


def plan_deferred(pr_body: str, runs: list[dict], jobs_of=lambda run_id: []) -> bool:
    """release.yml plan: auto-release merged this PR with hw-tests deferred (marker in the body), and hw-tests on the
    head has not failed since (a run that started after the merge may still be running)."""
    if DEFERRED_MARKER not in (pr_body or ""):
        return False
    hw = newest_hw_run(runs)
    if hw is None:
        return False
    return not (hw.get("status") == "completed" and hw.get("conclusion") != "success"
                and hw_started(hw, jobs_of(hw.get("id"))))


def deferred_body(body: str) -> str:
    """The release PR body with the deferral marker (idempotent)."""
    if DEFERRED_MARKER in (body or ""):
        return body
    return (f"{(body or '').rstrip()}\n\n{DEFERRED_MARKER}\n> **{DEFERRED_LINE}**: {HW_CHECK} did not start within "
            f"{HW_RUNNER_WAIT_MIN} min (runner offline), so this release merged on {', '.join(CLOUD_CHECKS)} (#848).\n")


def deferred_failure_title(release_pr: dict | None, failed_sha: str, hw_runs: list[dict], in_range) -> str | None:
    """followup: the P0 issue title when a hw-tests failure on `failed_sha` belongs to a deferred release.
    release_pr = the newest merged dev -> main release PR; in_range(sha) = release head <= sha <= failed_sha.
    None when the release was not deferred, the failure is outside that range, or a hw-tests success in the range
    already verified the release (a later failure is then an ordinary dev failure)."""
    if not release_pr or DEFERRED_MARKER not in (release_pr.get("body") or ""):
        return None
    if not in_range(failed_sha):
        return None
    if any(r.get("conclusion") == "success" and in_range(r.get("head_sha", "")) for r in hw_runs):
        return None
    tag = release_pr.get("title", "").split(":", 1)[-1].strip() or "?"
    return FAILED_TITLE.format(tag=tag)


def failure_issue_body(tag_title: str, run_url: str, head: str, failed_sha: str) -> str:
    return (f"Why: {tag_title.split(' on ', 1)[-1]} shipped with {DEFERRED_LINE} (#848: the tlwu-hw runner did not start "
            f"hw-tests within {HW_RUNNER_WAIT_MIN} min). The later hw-tests run failed: {run_url}\n"
            f"Goal: Find whether the released code is broken on real hardware and fix or roll back.\n"
            f"Acceptance: hw-tests is green on dev, or the bad release is rolled back per docs/RELEASING.md.\n"
            f"Verify: `gh run view` of the failed run above (released head {head[:12]}, failed commit "
            f"{failed_sha[:12]}), then a green hw-tests run on dev.\n")


# ---------------------------------------------------------------- gh / git I/O

def _gh_json(*args: str):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(r.stdout or "null")


def _ancestor(a: str, b: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", a, b], capture_output=True).returncode == 0


def followup(rep: str, failed_sha: str, run_url: str) -> str:
    closed = _gh_json("api", f"repos/{rep}/pulls?state=closed&base=main&sort=updated&direction=desc&per_page=50")
    rel = [p for p in closed if p.get("merged_at") and (p.get("head") or {}).get("ref") == "dev"
           and ((p.get("head") or {}).get("repo") or {}).get("full_name") == rep
           and p.get("title", "").strip().lower().startswith("release:")]
    pr = max(rel, key=lambda p: p["merged_at"]) if rel else None
    head = ((pr or {}).get("head") or {}).get("sha", "")
    runs = _gh_json("api", f"repos/{rep}/actions/workflows/hw-tests.yml/runs?branch=dev&status=success&per_page=100")
    in_range = lambda sha: bool(sha and head) and _ancestor(head, sha) and _ancestor(sha, failed_sha)
    title = deferred_failure_title(pr, failed_sha, runs.get("workflow_runs", []), in_range)
    if title is None:
        return "no issue: the hw-tests failure does not belong to a deferred release"
    for i in _gh_json("api", f"repos/{rep}/issues?state=open&labels=P0&per_page=100"):
        if i.get("title") == title:
            return f"no issue: #{i['number']} {title} is already open"
    args = ["api", "-X", "POST", f"repos/{rep}/issues", "-f", f"title={title}",
            "-f", f"body={failure_issue_body(title, run_url, head, failed_sha)}"]
    for label in ISSUE_LABELS:
        args += ["-f", f"labels[]={label}"]
    num = _gh_json(*args)["number"]
    return f"opened #{num} {title}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decide", help="dry run: the hw-tests gate state for a mocked run list")
    d.add_argument("--runs", required=True, help="JSON list of workflow runs (actions/runs?head_sha=...)")
    d.add_argument("--jobs", help="JSON object {run_id: [jobs]} (actions/runs/ID/jobs)")
    d.add_argument("--now", help="ISO time (default: now)")
    sub.add_parser("followup", help="after a failed hw-tests run (RUN_SHA, RUN_URL): open the P0 issue if needed")
    a = ap.parse_args(argv)
    if a.cmd == "decide":
        runs = json.loads(open(a.runs, encoding="utf-8").read())
        jobs = json.loads(open(a.jobs, encoding="utf-8").read()) if a.jobs else {}
        out = decide(runs, lambda rid: jobs.get(str(rid), []), _ts(a.now) if a.now else None)
        print(f"state={out['state']}\nreason={out['reason']}")
        return 0
    rep = os.environ.get("GITHUB_REPOSITORY") or "sunososobro-hub/tinglongwu"
    print(followup(rep, os.environ["RUN_SHA"], os.environ.get("RUN_URL", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
