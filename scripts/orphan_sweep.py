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
EMPTY_CLAIM_BRANCH = re.compile(r"^fix/(\d+)(?:-r\d+)?$")
EMPTY_CLAIM_GRACE_SEC = 5 * 60


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


def is_empty_claim(name: str, ahead_by: int, has_open_pr: bool, issue_labels: set[str] | None,
                   issue_updated_at: datetime | None, now: datetime) -> bool:
    """True when a `fix/N` (or `fix/N-rK`) branch is an empty leftover claim safe to delete."""
    if not EMPTY_CLAIM_BRANCH.match(name) or ahead_by != 0 or has_open_pr:
        return False
    if issue_labels is not None and "status:doing" in issue_labels:
        return False
    if issue_updated_at is not None and (now - issue_updated_at).total_seconds() <= EMPTY_CLAIM_GRACE_SEC:
        return False
    return True


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _gh(path: str, *args: str) -> object:
    out = subprocess.run(["gh", "api", path, *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else None


def _gh_list(path: str) -> list:
    """Every page of a collection, flattened (`--paginate --slurp` wraps the pages in one JSON array)."""
    pages = _gh(path, "--paginate", "--slurp") or []
    return [item for page in pages for item in (page if isinstance(page, list) else [page])]


def _issue_or_none(num: str) -> dict | None:
    """Issue record, None only on a confirmed 404/410; any other failure aborts."""
    proc = subprocess.run(["gh", "api", f"repos/{REPO}/issues/{num}"], capture_output=True, text=True)
    if proc.returncode == 0:
        return json.loads(proc.stdout)
    if "HTTP 404" in proc.stderr or "HTTP 410" in proc.stderr:
        return None
    raise subprocess.CalledProcessError(proc.returncode, proc.args, proc.stdout, proc.stderr)


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
    _sweep_empty_claims(now, args.apply)
    return 0


def _open_heads() -> set[str]:
    """Head branch names of open PRs from this repo (a fork PR with the same name protects nothing here)."""
    prs = _gh_list(f"repos/{REPO}/pulls?state=open&per_page=100")
    return {pr["head"]["ref"] for pr in prs if (pr["head"].get("repo") or {}).get("full_name") == REPO}


def _sweep_empty_claims(now: datetime, apply: bool) -> None:
    """Delete empty `fix/N` (or `fix/N-rK`) branches left behind by released claims."""
    open_heads = _open_heads()
    for br in _gh_list(f"repos/{REPO}/branches?per_page=100"):
        name = br["name"]
        m = EMPTY_CLAIM_BRANCH.match(name)
        if not m or name in open_heads:
            continue
        try:
            sha = _empty_claim_sha(name, m.group(1), open_heads, now)
            if sha is None:
                continue
            print(f"empty branch {name}")
            if not apply:
                continue
            # re-check right before deleting (fresh PR list too): a worker may have pushed, claimed or opened a PR
            if _empty_claim_sha(name, m.group(1), _open_heads(), now) != sha:
                continue
            _delete_at(name, sha)
        except subprocess.CalledProcessError as exc:
            print(f"warning: skipped {name}: {(exc.stderr or str(exc)).strip()}")


def _empty_claim_sha(name: str, num: str, open_heads: set[str], now: datetime) -> str | None:
    """Head SHA of `name` when it is an empty claim right now (compared with dev at that SHA), else None."""
    sha = _gh(f"repos/{REPO}/git/ref/heads/{name}")["object"]["sha"]
    ahead_by = _gh(f"repos/{REPO}/compare/dev...{sha}")["ahead_by"]
    if ahead_by != 0:
        return None
    issue = _issue_or_none(num)
    labels = {lb["name"] for lb in issue.get("labels", [])} if issue else None
    updated_at = _ts(issue["updated_at"]) if issue else None
    return sha if is_empty_claim(name, ahead_by, name in open_heads, labels, updated_at, now) else None


def _delete_at(name: str, sha: str, remote: str = "origin", cwd: str | None = None) -> None:
    """Delete the branch only if it still points at `sha`: git checks the lease atomically on the server side,
    so a commit pushed after the check makes the delete fail instead of losing that commit."""
    subprocess.run(["git", "push", remote, f"--force-with-lease=refs/heads/{name}:{sha}", f":refs/heads/{name}"],
                   check=True, capture_output=True, text=True, cwd=cwd)


if __name__ == "__main__":
    raise SystemExit(main())
