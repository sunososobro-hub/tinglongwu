"""#1886 (C5 of #1875): requeue a waiting PR on the event itself, not on the 30-min board scan.

Run by `.github/workflows/requeue-event.yml`. Rules only; it never checks out or runs PR code.

Events and rules (pure functions below, unit-tested in tests/test_requeue_event.py):
- push to dev: every open same-repo PR into dev whose mergeable_state is now `dirty` -> requeue "dirty".
- ci completed (workflow_run): a PR whose current head is the run's head SHA and whose `test` check on it is red
  -> requeue "test red". A run on an older head is ignored (a newer push is already on the way).
- review submitted by a review bot (`...[bot]`) on the PR's current head with a Critical / Major finding (review
  body or its inline comments) -> requeue "review Critical/Major". A review that lands inside the author's
  CodeRabbit window (AUTHOR_WINDOW_S after the PR opened: the worker sleeps 270 s once and handles the review
  itself, rules section 3) is left to that worker.

Requeue itself is board_scan's: only `fix/N*` heads whose issue #N is `status:review` (so a worker that is
already on it, `status:doing`, is never disturbed); `status:review` -> `status:todo` with a `requeue:` comment,
or `needs-opus` after MAX_REQUEUES. board-scan stays the safety net for anything an event missed.

Usage: python scripts/requeue_event.py --event push|ci|review [--pr N] [--sha SHA] [--review-id ID] [--apply]
(needs `gh` with GH_TOKEN; without --apply it only prints what it would do)
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board_scan as bs  # noqa: E402

REPO = bs.REPO
AUTHOR_WINDOW_S = 300  # worker: open PR, swap label, `sleep 270`, read the review once (rules section 3 / 20)
MERGEABLE_TRIES = 5
MERGEABLE_SLEEP_S = 6
# CodeRabbit marks each finding `<img ...></picture> Major** ...` (older: `_🟠 Major_`).
SEVERE_RE = re.compile(r"(?:</picture>\s*|_\S{1,3}\s+)(Critical|Major)(?![A-Za-z])")


# ---- rules (pure) -----------------------------------------------------------------------------------------

def same_repo(pr: dict) -> bool:
    return ((pr.get("head") or {}).get("repo") or {}).get("full_name") == REPO


def dirty_why(pr: dict) -> str | None:
    """After a dev push: "dirty" for an open same-repo PR into dev that now conflicts, else None."""
    if pr.get("state") != "open" or (pr.get("base") or {}).get("ref") != "dev" or not same_repo(pr):
        return None
    return "dirty" if pr.get("mergeable_state") == "dirty" else None


def ci_why(pr: dict, run_sha: str, test_conclusion: str | None) -> str | None:
    """After ci completed: "test red" when the run was on the PR's current head and `test` there is red."""
    if pr.get("state") != "open" or (pr.get("base") or {}).get("ref") != "dev" or not same_repo(pr):
        return None
    if pr["head"]["sha"] != run_sha:
        return None
    return "test red" if test_conclusion in bs.RED else None


def severe(texts: list[str]) -> bool:
    return any(SEVERE_RE.search(t or "") for t in texts)


def review_why(pr: dict, review: dict, comment_bodies: list[str]) -> str | None:
    """After a review: "review Critical/Major" for a review-bot review on the current head with such a finding,
    submitted after the author's own CodeRabbit window."""
    if pr.get("state") != "open" or (pr.get("base") or {}).get("ref") != "dev" or not same_repo(pr):
        return None
    if not ((review.get("user") or {}).get("login") or "").endswith("[bot]"):
        return None
    if review.get("commit_id") != pr["head"]["sha"]:
        return None  # a review of an older head: the newer push is already a fix round
    opened, at = review_time(pr.get("created_at")), review_time(review.get("submitted_at"))
    if opened is None or at is None or (at - opened).total_seconds() <= AUTHOR_WINDOW_S:
        return None
    return "review Critical/Major" if severe([review.get("body") or "", *comment_bodies]) else None


def review_time(s: str | None) -> datetime | None:
    return bs._ts(s) if s else None


# ---- GitHub I/O -------------------------------------------------------------------------------------------

def _pr(num: int) -> dict:
    return bs._gh(f"repos/{REPO}/pulls/{num}")


def _settled_pr(num: int) -> dict:
    """The PR once GitHub has computed mergeable_state (it does so lazily after a base push)."""
    pr = _pr(num)
    for _ in range(MERGEABLE_TRIES - 1):
        if pr.get("mergeable_state") not in (None, "unknown"):
            break
        time.sleep(MERGEABLE_SLEEP_S)
        pr = _pr(num)
    return pr


def _act(pr: dict, why: str | None, apply: bool) -> str | None:
    if not why:
        return None
    if not apply:
        return f"PR #{pr['number']} {why} (dry run)"
    done = bs._requeue(pr["number"], why, pr["head"]["ref"])
    return f"PR #{pr['number']} {why}: {done or 'no change (issue not in status:review)'}"


def on_push(apply: bool) -> list[str]:
    out = []
    for p in bs._gh_list(f"repos/{REPO}/pulls?state=open&base=dev&per_page=100"):
        if not same_repo(p):
            continue
        pr = _settled_pr(p["number"])
        line = _act(pr, dirty_why(pr), apply)
        if line:
            out.append(line)
    return out


def on_ci(nums: list[int], sha: str, apply: bool) -> list[str]:
    out = []
    for n in nums:
        pr = _pr(n)
        line = _act(pr, ci_why(pr, sha, bs._test_conclusion(sha)), apply)
        if line:
            out.append(line)
    return out


def on_review(num: int, review_id: int, apply: bool) -> list[str]:
    pr = _pr(num)
    review = bs._gh(f"repos/{REPO}/pulls/{num}/reviews/{review_id}")
    bodies = [c.get("body") or "" for c in
              bs._gh_list(f"repos/{REPO}/pulls/{num}/reviews/{review_id}/comments?per_page=100")]
    line = _act(pr, review_why(pr, review, bodies), apply)
    return [line] if line else []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", choices=["push", "ci", "review"], required=True)
    ap.add_argument("--pr", default="", help="PR number(s), comma separated (ci, review)")
    ap.add_argument("--sha", default="", help="head SHA of the ci run")
    ap.add_argument("--review-id", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    nums = [int(x) for x in a.pr.split(",") if x.strip()]
    if a.event == "push":
        lines = on_push(a.apply)
    elif a.event == "ci":
        lines = on_ci(nums, a.sha, a.apply) if nums and a.sha else []
    else:
        lines = on_review(nums[0], a.review_id, a.apply) if nums and a.review_id else []
    print("\n".join(lines) if lines else "requeue-event: nothing to do")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
