#!/usr/bin/env python3
"""Close the issue a merged dev PR says it closes (no AI, #1733). Run by .github/workflows/close-on-merge.yml.

GitHub only acts on `Closes #N` for the default branch (`main`); our PRs merge into `dev`, so issues stayed open
with `status:review` after their fix merged. Only `#N` matching the PR head (`fix/N`, `fix/N-rK`, `feat/N`) is
closed, so a stray number in a PR body cannot close an unrelated issue. `Refs #N` (partial work) never closes.
PR text is data: only digits are taken from it.
A merged `Refs #N` PR (partial work) puts #N from `status:review` back to `status:todo` so the next worker
finishes or closes it (owner 2026-11-25: merged issues sat in review with nobody moving them); `需實測`
issues stay in review for the owner's test.
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


def requeue_partial(repo: str, n: int, pr: int, token: str) -> str:
    """status:review -> status:todo after a partial merge (not for 需實測). Returns what happened."""
    issue = _api(f"/repos/{repo}/issues/{n}", token)
    if issue.get("pull_request") or issue.get("state") == "closed":
        return "skip"
    names = {lab["name"] for lab in issue.get("labels", [])}
    if OWNER_TEST in names or "status:review" not in names:
        return "skip"
    _api(f"/repos/{repo}/issues/{n}/labels/{urllib.parse.quote('status:review', safe='')}", token, "DELETE")
    _api(f"/repos/{repo}/issues/{n}/labels", token, "POST", {"labels": ["status:todo"]})
    _api(f"/repos/{repo}/issues/{n}/comments", token, "POST",
         {"body": f"requeue: partial PR #{pr} merged into dev; next worker finishes the remaining Acceptance "
                  "or closes this issue if it is all met."})
    return "requeued"


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


def main() -> int:
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
        pr = json.load(f)["pull_request"]
    if not pr.get("merged") or (pr.get("base") or {}).get("ref") != "dev":
        print("not a merge into dev: nothing to do")
        return 0
    head = (pr.get("head") or {}).get("ref")
    n = issue_to_close(pr.get("body"), head)
    if n is None:
        partial = issue_to_requeue(pr.get("body"), head)
        if partial is not None:
            result = requeue_partial(os.environ["GH_REPO"], partial, pr["number"], os.environ["GITHUB_TOKEN"])
            print(f"PR #{pr['number']} -> #{partial}: {result}")
            return 0
        print(f"PR #{pr['number']}: no Closes #N matching its head; nothing to do")
        return 0
    result = close_issue(os.environ["GH_REPO"], n, pr["number"], os.environ["GITHUB_TOKEN"])
    print(f"PR #{pr['number']} -> #{n}: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
