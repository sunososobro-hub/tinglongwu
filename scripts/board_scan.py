"""#1471: rule-only board scan, run by a scheduled Action every 30 min.

Posts one line on the `[health] pipeline check log` issue (#61) so the AI patrol only wakes when needed:
`[board HH:MM] ok` or `[board HH:MM] act: <items>` (HH:MM in Asia/Taipei).

Rules (each is a pure function below, unit-tested in tests/test_board_scan.py):
- PR waiting: an open PR to `dev` whose newest head commit is older than 30 min and whose `test` check is red,
  whose mergeable_state is `dirty`, or that carries `needs-opus`.
- Zombie branch: a `fix/N*` branch with 0 commits ahead of `dev`, never the head of any PR, issue #N open and not
  `status:doing` (leftovers of closed issues are the stale-branch cleanup's job).
- Triage: open issues labelled `needs-triage`.
- Dev red: the `test` check on the dev tip has been failing for more than 2 h (oldest failing commit in the
  current red streak is older than 2 h).

Usage: python scripts/board_scan.py [--post]   (needs `gh` with GH_TOKEN; default prints the line only)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from datetime import UTC, datetime, timedelta, timezone

REPO = "sunososobro-hub/tinglongwu"
LOG_ISSUE = 61
TPE = timezone(timedelta(hours=8))
PR_WAIT_MIN = 30
DEV_RED_MIN = 120
RED = {"failure", "cancelled", "timed_out"}
FIX_BRANCH = re.compile(r"^fix/(\d+)(?:-r\d+)?$")


# ---- rules (pure) -----------------------------------------------------------------------------------------

def pr_needs_action(now: datetime, head_commit_at: datetime, test_conclusion: str | None,
                    mergeable_state: str | None, labels: set[str]) -> str | None:
    """Reason a PR needs the patrol, or None. Only PRs waiting > 30 min since their newest head commit count."""
    if now - head_commit_at <= timedelta(minutes=PR_WAIT_MIN):
        return None
    if "needs-opus" in labels:
        return "needs-opus"
    if test_conclusion in RED:
        return "test red"
    if mergeable_state == "dirty":
        return "dirty"
    if mergeable_state in (None, "unknown"):
        return "mergeable unknown"  # never report ok while GitHub has not decided
    return None


def is_zombie_branch(name: str, ahead_by: int, had_pr: bool, issue_labels: set[str] | None) -> bool:
    """A `fix/N*` branch with no commits ahead of dev, never used by any PR, and no live claim on open issue #N.

    `had_pr` is True when any PR (open, closed or merged) ever used the branch as its head: merged or
    reviewed leftovers are not locks (rules section 2) and are not zombies.

    `issue_labels` is None when issue #N is closed or missing: such leftovers are the stale-branch
    cleanup's job, not the patrol's, so they never count.
    """
    if not FIX_BRANCH.match(name) or ahead_by > 0 or had_pr or issue_labels is None:
        return False
    return "status:doing" not in issue_labels


def needs_triage(labels: set[str]) -> bool:
    return "needs-triage" in labels


def dev_red_since(history: list[tuple[datetime, str | None]]) -> datetime | None:
    """Start of the current red streak of the `test` check on dev.

    `history` is (time the commit's `test` turned red, or commit time; test conclusion) in commit order,
    newest first. Commit order decides which commits are in the streak (it ends at the newest finished
    green); the earliest failure time inside it is the start (runs can finish out of order). Commits
    whose `test` has not finished (None) are skipped. None when the newest finished `test` is green.
    """
    times = []
    for when, concl in history:
        if concl is None:
            continue
        if concl not in RED:
            break
        times.append(when)
    return min(times) if times else None


def dev_red_too_long(now: datetime, history: list[tuple[datetime, str | None]]) -> bool:
    since = dev_red_since(history)
    return since is not None and now - since > timedelta(minutes=DEV_RED_MIN)


def board_line(now: datetime, items: list[str]) -> str:
    stamp = now.astimezone(TPE).strftime("%H:%M")
    return f"[board {stamp}] ok" if not items else f"[board {stamp}] act: " + "; ".join(items)


# ---- GitHub I/O -------------------------------------------------------------------------------------------

def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _gh(path: str, *args: str) -> object:
    out = subprocess.run(["gh", "api", path, *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else None


def _gh_list(path: str) -> list:
    pages = _gh(path, "--paginate", "--slurp") or []
    return [item for page in pages for item in (page if isinstance(page, list) else [page])]


def run_state(runs: list[dict]) -> tuple[str | None, datetime | None]:
    """(conclusion, red since) from all `test` check runs on one commit (reruns share the SHA).

    The conclusion is the newest run's; (None, None) while it is missing or running. When it is red,
    `red since` is the first red completion after the newest green attempt, so a failed rerun does not
    restart the streak.
    """
    runs = sorted(runs, key=lambda r: r.get("started_at") or "")
    if not runs or runs[-1].get("status") != "completed":
        return None, None
    concl = runs[-1].get("conclusion")
    if concl not in RED:
        return concl, None
    since = None
    for r in runs:
        if r.get("status") != "completed":
            continue
        if r.get("conclusion") in RED:
            if since is None and r.get("completed_at"):
                since = _ts(r["completed_at"])
        else:
            since = None  # a green attempt ends any earlier red
    return concl, since


def _test_run(sha: str) -> tuple[str | None, datetime | None]:
    # filter=all: every attempt (reruns share the SHA); the default `latest` would hide the first failure
    pages = _gh(f"repos/{REPO}/commits/{sha}/check-runs?check_name=test&filter=all&per_page=100",
                "--paginate", "--slurp") or []
    runs = [run for page in pages for run in (page.get("check_runs", []) if isinstance(page, dict) else [])]
    return run_state(runs)


def _test_conclusion(sha: str) -> str | None:
    return _test_run(sha)[0]


def zombie_issue_labels(issue: dict | None) -> set[str] | None:
    """Labels of issue #N for the zombie rule; None when #N is missing, closed or is a pull request."""
    if not issue or issue.get("state") != "open" or "pull_request" in issue:
        return None
    return {lb["name"] for lb in issue.get("labels", [])}


def _issue_or_none(num: str) -> dict | None:
    """Issue record, None only on a confirmed 404; any other failure aborts the scan (no false `ok`)."""
    proc = subprocess.run(["gh", "api", f"repos/{REPO}/issues/{num}"], capture_output=True, text=True)
    if proc.returncode == 0:
        return json.loads(proc.stdout)
    if "HTTP 404" in proc.stderr or "HTTP 410" in proc.stderr:
        return None
    raise subprocess.CalledProcessError(proc.returncode, proc.args, proc.stdout, proc.stderr)


def _dev_red_too_long(now: datetime) -> bool:
    """Walk dev commits newest first (pages of 100) until the red streak ends, then judge the whole streak.

    A red commit is timed by when its `test` run finished red, not by its commit date, so an old commit
    that only just failed does not count as hours of red.
    """
    history: list[tuple[datetime, str | None]] = []
    page = 1
    while True:
        commits = _gh(f"repos/{REPO}/commits?sha=dev&per_page=100&page={page}") or []
        for c in commits:
            concl, done = _test_run(c["sha"])
            history.append((done or _ts(c["commit"]["committer"]["date"]), concl))
            if concl is not None and concl not in RED:
                return dev_red_too_long(now, history)
        if len(commits) < 100:
            return dev_red_too_long(now, history)
        page += 1


def scan(now: datetime) -> list[str]:
    items: list[str] = []
    owner = REPO.split("/")[0]
    prs = _gh_list(f"repos/{REPO}/pulls?state=open&base=dev&per_page=100")
    # only same-repo heads shadow a local branch; a fork's fix/12 says nothing about ours
    open_heads = {pr["head"]["ref"] for pr in prs if (pr["head"].get("repo") or {}).get("full_name") == REPO}
    for pr in prs:
        full = _gh(f"repos/{REPO}/pulls/{pr['number']}")  # one revision: sha, labels and mergeable_state together
        if full.get("state") != "open" or full["base"]["ref"] != "dev":
            continue  # out of scope already: no retry
        if full.get("mergeable_state") in (None, "unknown"):
            time.sleep(3)  # GitHub computes mergeability lazily; ask once more before calling it unknown
            full = _gh(f"repos/{REPO}/pulls/{pr['number']}")
        if full.get("state") != "open" or full["base"]["ref"] != "dev":
            continue  # closed or retargeted since the list request (checked on the response we use)
        sha = full["head"]["sha"]
        head_at = _ts(_gh(f"repos/{REPO}/commits/{sha}")["commit"]["committer"]["date"])
        labels = {lb["name"] for lb in full["labels"]}
        why = pr_needs_action(now, head_at, _test_conclusion(sha), full.get("mergeable_state"), labels)
        if why:
            items.append(f"PR #{pr['number']} {why}")
    for br in _gh_list(f"repos/{REPO}/branches?per_page=100"):
        m = FIX_BRANCH.match(br["name"])
        if not m or br["name"] in open_heads:
            continue
        ahead = _gh(f"repos/{REPO}/compare/dev...{br['name']}")["ahead_by"]
        if ahead > 0:
            continue
        issue_labels = zombie_issue_labels(_issue_or_none(m.group(1)))
        if issue_labels is None or "status:doing" in issue_labels:
            continue
        had_pr = bool(_gh(f"repos/{REPO}/pulls?state=all&head={owner}:{br['name']}&per_page=1"))
        if is_zombie_branch(br["name"], ahead, had_pr, issue_labels):
            items.append(f"zombie {br['name']}")
    for it in _gh_list(f"repos/{REPO}/issues?state=open&labels=needs-triage&per_page=100"):
        if "pull_request" not in it:
            items.append(f"triage #{it['number']}")
    if _dev_red_too_long(now):
        items.append("dev test red > 2 h")
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--post", action="store_true", help=f"post the line on #{LOG_ISSUE}")
    args = ap.parse_args()
    now = datetime.now(UTC)
    line = board_line(now, scan(now))
    print(line)
    if args.post:
        _gh(f"repos/{REPO}/issues/{LOG_ISSUE}/comments", "-X", "POST", "-f", f"body={line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
