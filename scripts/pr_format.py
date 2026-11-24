"""#1640: rule-only PR format lint for PRs into `dev` (rules sections 2 and 14), run by `pr-format.yml`.

Checks the three format rules every PR into dev must meet:
  head   `fix/N`, `fix/N-rK` or `feat/N` (hotfix/*, release/* and the section 11 `main` sync head are exempt);
  link   `Closes #N` or `Refs #N` in the body (HTML comments, e.g. the template's hints, do not count);
  usage  a `tlwu-usage: ... minutes=N` line (same regex as merge_gate.py).

What happens on a miss (owner amendment 2026-11-19 on #1640):
  collaborator PR (owner / member / collaborator, same-repo head): one comment listing the misses, then closed
      right away, so it never reaches the AI mergers;
  outside PR (fork or non-collaborator): never closed on format; one friendly comment with a copy-paste fix and
      the `needs-format` label. A `needs-format` PR with no update for 7 days is closed with a thank-you note
      (`--stale`, run on a schedule). Once fixed, the label is removed.
It never checks out or runs PR code and uses no AI: it reads the PR through the REST API only.

Usage: python scripts/pr_format.py (--pr N | --stale) [--apply]   (needs `gh` with GH_TOKEN; default dry run)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _merge_gate():
    spec = importlib.util.spec_from_file_location("merge_gate", Path(__file__).with_name("merge_gate.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mg = _merge_gate()
REPO = _mg.REPO
HEAD_RE, LINK_RE, USAGE_RE = _mg.HEAD_RE, _mg.LINK_RE, _mg.USAGE_RE
NEEDS_FORMAT = "needs-format"
MARKER = "<!-- pr-format -->"
STALE_DAYS = 7  # owner amendment 2026-11-19 (#1640): close an outside needs-format PR after 7 days with no update
TRUSTED_ASSOC = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
EXEMPT_HEAD_RE = re.compile(r"^(?:hotfix/|release/|main$)")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)

FIX_TEXT = {
    "head": "the branch name is not `fix/<issue>` (or `fix/<issue>-r2`, `feat/<issue>`): "
            "push the same commits to `fix/<issue number>` and open the PR from that branch",
    "link": "the description has no issue link: add a line `Closes #<issue number>` "
            "(or `Refs #<issue number>` for partial work)",
    "usage": "the description has no usage line: add as the last line "
             "`tlwu-usage: model=human minutes=<minutes spent>`",
}


def _body(body: str | None) -> str:
    """The PR body without HTML comments (template hints) and review-bot blocks."""
    return HTML_COMMENT_RE.sub("", _mg.clean_body(body))


def missing(pr: dict) -> list[str]:
    """Format rules this PR misses, in the order head, link, usage. Empty = compliant."""
    out = []
    if not HEAD_RE.match(pr["head"]["ref"]):
        out.append("head")
    body = _body(pr.get("body"))
    if not LINK_RE.search(body):
        out.append("link")
    if not any(USAGE_RE.match(x.strip()) for x in body.splitlines()):
        out.append("usage")
    return out


def is_collaborator(pr: dict) -> bool:
    same_repo = (pr["head"].get("repo") or {}).get("full_name") == REPO
    return same_repo and pr.get("author_association") in TRUSTED_ASSOC


def decide(pr: dict) -> tuple[str, list[str]]:
    """Pure decision for one PR: (action, misses).

    skip   not an open, non-draft PR into dev by a user, or an exempt head (hotfix / release / main sync);
    ok     compliant (remove `needs-format` if it is there);
    close  collaborator PR with a miss: comment + close now;
    label  outside PR with a miss: friendly comment + `needs-format`, never closed on format."""
    if pr.get("state", "open") != "open" or pr["base"]["ref"] != "dev" or pr.get("draft"):
        return "skip", []
    if (pr.get("user") or {}).get("type") == "Bot" or EXEMPT_HEAD_RE.match(pr["head"]["ref"]):
        return "skip", []
    miss = missing(pr)
    if not miss:
        return "ok", []
    return ("close" if is_collaborator(pr) else "label"), miss


def comment_text(action: str, miss: list[str]) -> str:
    items = "\n".join(f"- {FIX_TEXT[m]}" for m in miss)
    if action == "close":
        return (f"{MARKER}\npr-format: closed, this PR misses the section 2 format "
                f"(.agent/tlwu_rules.md): {', '.join(miss)}. Reopen as a new PR with:\n{items}")
    return (f"{MARKER}\nThanks for the PR! Our merge bot needs a few small things before a maintainer can "
            f"review it. Please fix these (edit the description; the bot re-checks on its own):\n{items}\n\n"
            "Example description ending:\n\n```\nCloses #123\n\ntlwu-usage: model=human minutes=30\n```\n\n"
            "Not sure about the branch name? Leave it: a maintainer can re-push your commits as `fix/<issue>`. "
            f"This PR stays open; it is only closed if nothing changes for {STALE_DAYS} days.")


def stale(pr: dict, now: datetime) -> bool:
    """An outside `needs-format` PR with no update for STALE_DAYS days (it is closed with a thank-you note)."""
    labels = {lb["name"] for lb in pr.get("labels", [])}
    if NEEDS_FORMAT not in labels or pr.get("state", "open") != "open" or pr["base"]["ref"] != "dev":
        return False
    updated = datetime.fromisoformat(pr["updated_at"].replace("Z", "+00:00"))
    return now - updated >= timedelta(days=STALE_DAYS)


STALE_TEXT = (f"{MARKER}\nThank you for your contribution! Closing this PR because the format fixes above got no "
              f"update for {STALE_DAYS} days. You are welcome to reopen it (or a new one) any time once they are in.")

# ---------------------------------------------------------------- I/O (gh only)


def _gh(*args: str):
    out = subprocess.run(["gh", "api", *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else None


def _has_marker(num: int) -> bool:
    return any(MARKER in (c.get("body") or "") for c in _gh(f"repos/{REPO}/issues/{num}/comments?per_page=100") or [])


def apply(num: int, pr: dict, action: str, miss: list[str]) -> None:
    labels = {lb["name"] for lb in pr.get("labels", [])}
    if action == "ok":
        if NEEDS_FORMAT in labels:
            _gh("-X", "DELETE", f"repos/{REPO}/issues/{num}/labels/{NEEDS_FORMAT}")
    elif action == "close":
        _gh("-X", "POST", f"repos/{REPO}/issues/{num}/comments", "-f", f"body={comment_text(action, miss)}")
        _gh("-X", "PATCH", f"repos/{REPO}/pulls/{num}", "-f", "state=closed")
        for lb in (_mg.NEEDS_OPUS, _mg.NEEDS_AI):  # merge-gate may have raced us on `opened`
            if lb in labels:
                _gh("-X", "DELETE", f"repos/{REPO}/issues/{num}/labels/{lb}")
    elif action == "label":
        if not _has_marker(num):
            _gh("-X", "POST", f"repos/{REPO}/issues/{num}/comments", "-f", f"body={comment_text(action, miss)}")
        if NEEDS_FORMAT not in labels:
            _gh("-X", "POST", f"repos/{REPO}/issues/{num}/labels", "-f", f"labels[]={NEEDS_FORMAT}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pr", type=int, help="check this PR")
    g.add_argument("--stale", action="store_true", help="close outside needs-format PRs idle for 7 days")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.stale:
        now = datetime.now(UTC)
        for pr in _gh(f"repos/{REPO}/pulls?state=open&base=dev&per_page=100") or []:
            if stale(pr, now):
                print(f"#{pr['number']} stale: closing")
                if a.apply:
                    _gh("-X", "POST", f"repos/{REPO}/issues/{pr['number']}/comments", "-f", f"body={STALE_TEXT}")
                    _gh("-X", "PATCH", f"repos/{REPO}/pulls/{pr['number']}", "-f", "state=closed")
        return 0
    pr = _gh(f"repos/{REPO}/pulls/{a.pr}")
    action, miss = decide(pr)
    print(f"#{a.pr} {action}: {', '.join(miss) or '-'}")
    if a.apply:
        apply(a.pr, pr, action, miss)
    return 0


if __name__ == "__main__":
    sys.exit(main())
