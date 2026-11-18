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
from datetime import datetime, UTC

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


def _gh_list(path: str) -> list:
    """Every page of a collection, flattened (`--paginate --slurp` wraps the pages in one JSON array)."""
    pages = _gh(path, "--paginate", "--slurp") or []
    return [item for page in pages for item in (page if isinstance(page, list) else [page])]


def _claim_time(num: int) -> datetime | None:
    """When `status:doing` was last added (issue events), newest first."""
    events = _gh_list(f"repos/{REPO}/issues/{num}/events?per_page=100")
    times = [_ts(e["created_at"]) for e in events
             if e.get("event") == "labeled" and e.get("label", {}).get("name") == "status:doing"]
    return max(times) if times else None


def _open_pr(num: int) -> tuple[bool, datetime | None]:
    """(has open PR, newest head commit time) for the PR whose body mentions #num or head is fix/num*."""
    found, newest = False, None
    for pr in _gh_list(f"repos/{REPO}/pulls?state=open&per_page=100"):
        body = pr.get("body") or ""
        head = pr["head"]["ref"]
        if re.match(rf"fix/{num}(-r\d+)?$", head) or re.search(rf"(?:Closes|Refs) #{num}\b", body):
            commits = _gh_list(f"repos/{REPO}/pulls/{pr['number']}/commits?per_page=100")
            last = max((_ts(c["commit"]["committer"]["date"]) for c in commits), default=None)
            found = True
            if last is not None and (newest is None or last > newest):
                newest = last
    return found, newest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    now = datetime.now(UTC)
    issues = _gh_list(f"repos/{REPO}/issues?state=open&labels=status:doing&per_page=100")
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
            # re-check right before writing: skip if the claim changed meanwhile, and only touch the status labels
            now_labels = {lb["name"] for lb in _gh(f"repos/{REPO}/issues/{it['number']}")["labels"]}
            has_pr, head_at = _open_pr(it["number"])  # a commit pushed since the scan keeps the claim
            if not is_orphan(now_labels, claimed, now, has_pr, head_at) or _claim_time(it["number"]) != claimed:
                continue
            _gh(f"repos/{REPO}/issues/{it['number']}/labels", "-X", "POST", "-f", "labels[]=status:todo")
            _gh(f"repos/{REPO}/issues/{it['number']}/labels/status:doing", "-X", "DELETE")
            _gh(f"repos/{REPO}/issues/{it['number']}/comments", "-X", "POST", "-f",
                "body=Orphan claim released (no PR / new commit within the timeout).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
