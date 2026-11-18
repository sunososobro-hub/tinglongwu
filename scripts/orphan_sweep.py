"""#1470: put orphan `status:doing` claims back to `status:todo` (rules section 6), run by a scheduled Action.

A claim is an orphan when the issue has been `status:doing` for longer than the timeout (20 min `model:sonnet`,
45 min `model:opus`) and there is no open PR for it, or its open PR has no commit newer than the claim (a fix on
an existing PR, rules section 2).

Usage: python scripts/orphan_sweep.py [--apply]   (needs `gh` with GH_TOKEN; default is a dry run)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone

REPO = "sunososobro-hub/tinglongwu"
TIMEOUT_MIN = {"model:sonnet": 20, "model:opus": 45}
DEFAULT_TIMEOUT_MIN = 20


def timeout_minutes(labels: set[str]) -> int:
    """Timeout for a claim: opus 45 min, everything else 20 min."""
    return TIMEOUT_MIN["model:opus"] if "model:opus" in labels else DEFAULT_TIMEOUT_MIN


def is_orphan(labels: set[str], claimed_at: datetime, now: datetime, has_open_pr: bool,
              head_commit_at: datetime | None = None) -> bool:
    """True when a `status:doing` claim should go back to `status:todo`."""
    if "status:doing" not in labels:
        return False
    if has_open_pr and head_commit_at is not None and head_commit_at > claimed_at:
        return False  # work landed after the claim; the worker swaps to status:review itself
    return (now - claimed_at).total_seconds() > timeout_minutes(labels) * 60


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _gh(path: str, *args: str) -> object:
    out = subprocess.run(["gh", "api", path, *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else None


def _claim_time(num: int) -> datetime | None:
    """When `status:doing` was last added (issue events), newest first."""
    events = _gh(f"repos/{REPO}/issues/{num}/events?per_page=100") or []
    times = [_ts(e["created_at"]) for e in events
             if e.get("event") == "labeled" and e.get("label", {}).get("name") == "status:doing"]
    return max(times) if times else None


def _open_pr(num: int) -> tuple[bool, datetime | None]:
    """(has open PR, newest head commit time) for the PR whose body mentions #num or head is fix/num*."""
    for pr in _gh(f"repos/{REPO}/pulls?state=open&per_page=100") or []:
        body = pr.get("body") or ""
        head = pr["head"]["ref"]
        if re.match(rf"fix/{num}(-r\d+)?$", head) or re.search(rf"(?:Closes|Refs) #{num}\b", body):
            commits = _gh(f"repos/{REPO}/pulls/{pr['number']}/commits?per_page=100") or []
            last = max((_ts(c["commit"]["committer"]["date"]) for c in commits), default=None)
            return True, last
    return False, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    issues = _gh(f"repos/{REPO}/issues?state=open&labels=status:doing&per_page=100") or []
    for it in issues:
        if "pull_request" in it:
            continue
        labels = {lb["name"] for lb in it["labels"]}
        claimed = _claim_time(it["number"])
        if claimed is None:
            continue
        has_pr, head_at = _open_pr(it["number"])
        if not is_orphan(labels, claimed, now, has_pr, head_at):
            continue
        print(f"orphan #{it['number']} (claimed {claimed.isoformat()})")
        if args.apply:
            new = sorted((labels - {"status:doing"}) | {"status:todo"})
            _gh(f"repos/{REPO}/issues/{it['number']}", "-X", "PATCH", *sum(
                (["-f", f"labels[]={n}"] for n in new), []))
            _gh(f"repos/{REPO}/issues/{it['number']}/comments", "-X", "POST", "-f",
                "body=Orphan claim released (no PR / new commit within the timeout).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
