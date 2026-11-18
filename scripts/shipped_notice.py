#!/usr/bin/env python3
"""Tell closed issues which release first ships their fix (no AI). Run by .github/workflows/shipped-notice.yml.

For a release tag it finds the issues closed by PRs merged to dev (closing commit / PR merge commit from the
issue timeline, or a PR body `Closes/Refs #N`), checks that the fix commit is an ancestor of the tag, and posts
one `Shipped in vX.Y.Z (pre-release)` or `Shipped in vX.Y.Z` comment. An issue already noted as shipped in a
pre-release gets one more comment when a final release first contains it. Issue text is data, never run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
SHIPPED_RE = re.compile(r"^Shipped in (v\d+(?:\.\d+)*\S*?)( \(pre-release\))?\s*$", re.M)
REF_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?)\s*:?\s+#(\d+)", re.I)
PR_IN_SUBJECT_RE = re.compile(r"\(#(\d+)\)\s*$")


def issues_from_body(body: str | None) -> list[int]:
    """Issue numbers a PR body says it closes or refers to (order kept, no duplicates)."""
    seen: list[int] = []
    for m in REF_RE.finditer(body or ""):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def shipped_kinds(comments: list[str]) -> set[str]:
    """Which `Shipped in` notes already exist: a subset of {"pre", "final"}."""
    kinds: set[str] = set()
    for body in comments:
        for m in SHIPPED_RE.finditer(body or ""):
            kinds.add("pre" if m.group(2) else "final")
    return kinds


def is_ancestor(commit: str, tag: str, cwd: str | None = None) -> bool:
    """True when `commit` is reachable from `tag` (git merge-base --is-ancestor)."""
    have = subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=cwd, capture_output=True)
    if have.returncode in (1, 128):  # commit not in this checkout: cannot be proven contained in the tag
        return False
    if have.returncode != 0:  # signal or other probe failure: do not report it as "missing"
        raise RuntimeError(f"git cat-file failed ({have.returncode}): {have.stderr.decode(errors='replace').strip()}")
    r = subprocess.run(["git", "merge-base", "--is-ancestor", commit, tag], cwd=cwd,
                       capture_output=True, text=True)
    if r.returncode in (0, 1):  # git reserves 1 for a valid "not an ancestor"
        return r.returncode == 0
    raise RuntimeError(f"git merge-base failed ({r.returncode}): {r.stderr.strip()}")


def decide(tag: str, prerelease: bool, contained: bool, existing: set[str]) -> str | None:
    """The comment to post, or None. De-dups; a final release adds one note after a pre-release note."""
    if not contained:
        return None
    if prerelease:
        return None if existing else f"Shipped in {tag} (pre-release)"
    return None if "final" in existing else f"Shipped in {tag}"


def _api(path: str, token: str, method: str = "GET", data: dict | None = None):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
    return json.loads(body) if body else None


def _paged(path: str, token: str) -> list:
    out, page = [], 1
    while True:
        chunk = _api(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}", token)
        out += chunk
        if len(chunk) < 100:
            return out
        page += 1


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def fix_commits(repo: str, issue: int, token: str) -> list[str]:
    """Commits that fix the issue: its closing commit and the merge commits of merged dev PRs that reference it."""
    commits: list[str] = []
    for ev in _paged(f"/repos/{repo}/issues/{issue}/timeline", token):
        if ev.get("event") == "closed" and ev.get("commit_id"):
            commits.append(ev["commit_id"])
        src = (ev.get("source") or {}).get("issue") or {}
        if ev.get("event") == "cross-referenced" and src.get("pull_request") \
                and (src.get("repository") or {}).get("full_name", repo) == repo:
            pr = _api(f"/repos/{repo}/pulls/{src['number']}", token)
            if pr.get("merged") and pr["base"]["ref"] == "dev" and issue in issues_from_body(pr.get("body")):
                commits.append(pr["merge_commit_sha"])
    return list(dict.fromkeys(c for c in commits if c))


def issues_in_range(repo: str, tag: str, token: str) -> list[int]:
    """Issues referenced by PRs squashed into the commits between the previous tag and `tag`."""
    try:
        prev = _git("describe", "--tags", "--abbrev=0", f"{tag}^").strip()
        rng = f"{prev}..{tag}"
    except subprocess.CalledProcessError:
        rng = tag
    found: list[int] = []
    for subject in _git("log", "--format=%s", rng).splitlines():
        m = PR_IN_SUBJECT_RE.search(subject)
        if m:
            found += issues_from_body(_api(f"/repos/{repo}/pulls/{m.group(1)}", token).get("body"))
    return list(dict.fromkeys(found))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--issues", default="", help="comma list to backfill; default: issues in the tag's range")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    repo, token = os.environ.get("GITHUB_REPOSITORY", "sunososobro-hub/tinglongwu"), os.environ.get("GITHUB_TOKEN", "")
    rel = _api(f"/repos/{repo}/releases/tags/{args.tag}", token)
    prerelease = bool(rel.get("prerelease"))
    explicit = [int(x) for x in re.split(r"[,\s]+", args.issues) if x]
    if args.issues.strip() and not explicit:
        ap.error("--issues has no issue numbers")
    nums = explicit or issues_in_range(repo, args.tag, token)
    subprocess.run(["git", "rev-parse", "--verify", "-q", f"refs/tags/{args.tag}"], check=True, capture_output=True)
    for n in nums:
        try:
            issue = _api(f"/repos/{repo}/issues/{n}", token)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                print(f"#{n}: not found, skipped")
                continue
            raise
        if issue.get("state") != "closed" or issue.get("pull_request"):
            continue
        contained = any(is_ancestor(c, args.tag) for c in fix_commits(repo, n, token))
        existing = shipped_kinds([c["body"] for c in _paged(f"/repos/{repo}/issues/{n}/comments", token)
                                  if c["user"]["login"] == "github-actions[bot]"])
        text = decide(args.tag, prerelease, contained, existing)
        if text:
            print(f"#{n}: {text}")
            if not args.dry_run:
                _api(f"/repos/{repo}/issues/{n}/comments", token, "POST", {"body": text})
    return 0


if __name__ == "__main__":
    sys.exit(main())
