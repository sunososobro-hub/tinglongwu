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

    `history` is (commit time, test conclusion) newest first. Commits whose `test` has not finished
    (None) are skipped. Returns None when the newest finished `test` is green.
    """
    since = None
    for when, concl in history:
        if concl is None:
            continue
        if concl in RED:
            since = when
        else:
            break
    return since


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


def _test_conclusion(sha: str) -> str | None:
    """Conclusion of the newest `test` check run on a commit; None when missing or not completed."""
    runs = (_gh(f"repos/{REPO}/commits/{sha}/check-runs?check_name=test&per_page=100") or {}).get("check_runs", [])
    runs = sorted(runs, key=lambda r: r.get("started_at") or "", reverse=True)
    if not runs or runs[0].get("status") != "completed":
        return None
    return runs[0].get("conclusion")


def scan(now: datetime) -> list[str]:
    items: list[str] = []
    prs = _gh_list(f"repos/{REPO}/pulls?state=open&base=dev&per_page=100")
    open_heads = {pr["head"]["ref"] for pr in prs}
    for pr in prs:
        if pr.get("draft"):
            continue
        full = _gh(f"repos/{REPO}/pulls/{pr['number']}")
        head = _gh(f"repos/{REPO}/commits/{pr['head']['sha']}")
        head_at = _ts(head["commit"]["committer"]["date"])
        labels = {lb["name"] for lb in pr["labels"]}
        why = pr_needs_action(now, head_at, _test_conclusion(pr["head"]["sha"]), full.get("mergeable_state"), labels)
        if why:
            items.append(f"PR #{pr['number']} {why}")
    for br in _gh_list(f"repos/{REPO}/branches?per_page=100"):
        m = FIX_BRANCH.match(br["name"])
        if not m or br["name"] in open_heads:
            continue
        ahead = _gh(f"repos/{REPO}/compare/dev...{br['name']}")["ahead_by"]
        if ahead > 0:
            continue
        try:
            issue = _gh(f"repos/{REPO}/issues/{m.group(1)}")
        except subprocess.CalledProcessError:
            issue = None
        issue_labels = ({lb["name"] for lb in issue["labels"]}
                        if issue and issue.get("state") == "open" else None)
        if issue_labels is None or "status:doing" in issue_labels:
            continue
        had_pr = bool(_gh(f"repos/{REPO}/pulls?state=all&head={REPO.split('/')[0]}:{br['name']}&per_page=1"))
        if is_zombie_branch(br["name"], ahead, had_pr, issue_labels):
            items.append(f"zombie {br['name']}")
    for it in _gh_list(f"repos/{REPO}/issues?state=open&labels=needs-triage&per_page=100"):
        if "pull_request" not in it:
            items.append(f"triage #{it['number']}")
    commits = _gh(f"repos/{REPO}/commits?sha=dev&per_page=20") or []
    history = [(_ts(c["commit"]["committer"]["date"]), _test_conclusion(c["sha"])) for c in commits]
    if dev_red_too_long(now, history):
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
