#!/usr/bin/env python3
"""Close the issue a merged dev PR says it closes (no AI, #1733), or put the issue of a partial PR back in the
queue (#1853). Run by .github/workflows/close-on-merge.yml.

GitHub only acts on `Closes #N` for the default branch (`main`); our PRs merge into `dev`, so issues stayed open
with `status:review` after their fix merged. Only `#N` matching the PR head (`fix/N`, `fix/N-rK`, `feat/N`) is
closed, so a stray number in a PR body cannot close an unrelated issue. `Refs #N` (partial work) never closes.
#1853: a merged `Refs #N` PR used to leave #N in `status:review` for good (24 of 29 such issues had no open PR and
pickers only read `status:todo`). It now goes back to `status:todo` (rules section 3, partial work), or to
`status:needs-test` when it carries `需實測` (rules section 1: only the hands-on check is left).
PR text is data: only digits are taken from it.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
REFS_RE = re.compile(r"\b(?:refs?|part of)\s*:?\s+#(\d+)\b", re.I)
OWNER_TEST = "需實測"
CLOSE_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b", re.I)
HEAD_RE = re.compile(r"^(?:fix|feat)/(\d+)(?:-r\d+)?$")
DROP_LABELS = ("status:review", "status:doing", "status:todo", "status:blocked")
DONE_LABEL = "status:done"
CLAIM_LABELS = ("status:review", "status:doing")  # what a worker's claim / open PR puts on the issue
PARKED_LABELS = ("status:frozen", "status:blocked", "status:proposal")  # someone parked it: add no status
TODO_LABEL = "status:todo"
NEEDS_TEST_LABEL = "status:needs-test"
HANDS_ON_LABEL = "需實測"


def issue_to_close(body: str | None, head_ref: str | None) -> int | None:
    """The issue number this PR closes, or None (head not fix/N, or body has no closing keyword for N)."""
    m = HEAD_RE.match(head_ref or "")
    if not m:
        return None
    n = int(m.group(1))
    wanted = {int(x) for x in CLOSE_RE.findall(body or "")}
    return n if n in wanted else None


def issue_to_requeue(body: str | None, head_ref: str | None) -> int | None:
    """The issue a partial (`Refs #N`) PR leaves open, or None. Closing keywords win over Refs."""
    m = HEAD_RE.match(head_ref or "")
    if not m or issue_to_close(body, head_ref) is not None:
        return None
    n = int(m.group(1))
    return n if n in {int(x) for x in REFS_RE.findall(body or "")} else None


def _api(path: str, token: str, method: str = "GET", data: dict | None = None):
    req = urllib.request.Request(API + path, method=method,
                                 data=None if data is None else json.dumps(data).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def close_issue(repo: str, n: int, pr: int, token: str) -> str:
    """Close #n as completed with status:done. Returns what happened (one word)."""
    issue = _api(f"/repos/{repo}/issues/{n}", token)
    if issue.get("pull_request"):
        return "is-pr"
    if issue.get("state") == "closed":
        return "already-closed"
    names = {lab["name"] for lab in issue.get("labels", [])}
    for lab in DROP_LABELS:
        if lab in names:
            try:
                _api(f"/repos/{repo}/issues/{n}/labels/{urllib.parse.quote(lab, safe='')}", token, "DELETE")
            except urllib.error.HTTPError as e:
                if e.code != 404:  # label vanished in between: fine
                    raise
    _api(f"/repos/{repo}/issues/{n}/labels", token, "POST", {"labels": [DONE_LABEL]})
    _api(f"/repos/{repo}/issues/{n}/comments", token, "POST",
         {"body": f"Closed by #{pr} merged into dev (merges into dev do not auto-close issues, #1733)."})
    _api(f"/repos/{repo}/issues/{n}", token, "PATCH", {"state": "closed", "state_reason": "completed"})
    return "closed"


def _delete_label(repo: str, n: int, lab: str, token: str) -> None:
    try:
        _api(f"/repos/{repo}/issues/{n}/labels/{urllib.parse.quote(lab, safe='')}", token, "DELETE")
    except urllib.error.HTTPError as e:
        if e.code != 404:  # label vanished in between: fine
            raise


def release_partial(repo: str, n: int, pr: int, token: str) -> str:
    """Move #n out of `status:review` / `status:doing` after its partial PR merged. Returns what happened.

    Resumable: the destination label and the one PR comment are written BEFORE the claim labels are removed, and the
    claim labels are the "still to do" marker, so a rerun after a failed call finishes the job without a second comment.
    """
    issue = _api(f"/repos/{repo}/issues/{n}", token)
    if issue.get("pull_request"):
        return "is-pr"
    if issue.get("state") == "closed":
        return "already-closed"
    names = {lab["name"] for lab in issue.get("labels", [])}
    if not any(lab in names for lab in CLAIM_LABELS):
        return "no-claim"  # not in review / doing (already todo, or moved by hand): leave it alone
    if any(lab in names for lab in PARKED_LABELS):
        target = "parked"
    else:
        target = NEEDS_TEST_LABEL if HANDS_ON_LABEL in names else TODO_LABEL
        marker = f"<!-- close-on-merge:partial:{pr} -->"
        if target not in names:
            _api(f"/repos/{repo}/issues/{n}/labels", token, "POST", {"labels": [target]})
        comments = _api(f"/repos/{repo}/issues/{n}/comments?per_page=100", token) or []
        if not any(marker in (c.get("body") or "") for c in comments):
            if target == NEEDS_TEST_LABEL:
                note = (f"#{pr} merged into dev (`Refs #{n}`): the code is in. `{NEEDS_TEST_LABEL}`: only the hands-on "
                        "check from the how-to-test comment is left.")
            else:
                note = (f"#{pr} merged into dev as partial work (`Refs #{n}`), so this issue is back in "
                        f"`{TODO_LABEL}`. Next worker: check each Acceptance line against dev. All met: close this "
                        f"issue with the evidence. Otherwise do the rest on the next free `fix/{n}-rK` "
                        "(rules sections 2 and 3).")
            _api(f"/repos/{repo}/issues/{n}/comments", token, "POST", {"body": f"{note}\n\n{marker}"})
    for lab in CLAIM_LABELS:  # last: only now is the release complete
        if lab in names:
            _delete_label(repo, n, lab, token)
    return target


def main() -> int:
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
        pr = json.load(f)["pull_request"]
    if not pr.get("merged") or (pr.get("base") or {}).get("ref") != "dev":
        print("not a merge into dev: nothing to do")
        return 0
    body, head = pr.get("body"), (pr.get("head") or {}).get("ref")
    n = issue_to_close(body, head)
    if n is None:
        n = issue_to_requeue(body, head)
        if n is None:
            print(f"PR #{pr['number']}: no Closes / Refs #N matching its head; nothing to do")
            return 0
        result = release_partial(os.environ["GH_REPO"], n, pr["number"], os.environ["GITHUB_TOKEN"])
        print(f"PR #{pr['number']} -> #{n} (partial): {result}")
        return 0
    result = close_issue(os.environ["GH_REPO"], n, pr["number"], os.environ["GITHUB_TOKEN"])
    print(f"PR #{pr['number']} -> #{n}: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
