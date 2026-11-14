#!/usr/bin/env python3
"""Delete stale remote branches (#344). Stdlib only, GITHUB_TOKEN only.

Usage: python scripts/stale_branches.py --repo OWNER/NAME [--dry-run] [--keep a,b] [--days 7] [--summary FILE]

A branch is deleted when either
  (a) it has PRs, none of them open, and its tip is still the head of its most recent PR
      (nobody pushed after the PR was closed or merged), or
  (b) it has no PR, has no commits that are not on dev (ahead_by == 0) and its tip is older than --days.
Never deleted: main, dev, gh-pages, --keep names, GitHub-protected branches, any branch with an open PR,
and a `fix/N*` lock branch whose issue N is open with `status:doing` (a worker may be about to push).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ALWAYS_KEEP = ("main", "dev", "gh-pages")


def http_api(method: str, path: str):
    """`method` https://api.github.com<path> -> parsed JSON (None for an empty body)."""
    req = urllib.request.Request("https://api.github.com" + path, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
    return json.loads(body) if body else None


def _pages(api, path: str, limit: int = 50) -> list:
    sep = "&" if "?" in path else "?"
    out: list = []
    for page in range(1, limit + 1):
        chunk = api("GET", "%s%sper_page=100&page=%d" % (path, sep, page))
        out.extend(chunk)
        if len(chunk) < 100:
            break
    return out


def _ref(name: str) -> str:
    return urllib.parse.quote(name, safe="/")


def _lock_issue(name: str) -> int | None:
    m = re.fullmatch(r"fix/(\d+)(?:-r\d+)?", name)
    return int(m.group(1)) if m else None


def plan(api, repo: str, keep=(), days: int = 7, now: datetime | None = None) -> list[tuple[str, str, str]]:
    """[(branch, sha, reason)] of branches to delete. Read-only."""
    now = now or datetime.now(timezone.utc)
    keep = set(ALWAYS_KEEP) | set(keep)
    branches = _pages(api, "/repos/%s/branches" % repo)
    prs: dict[str, list] = {}
    for pr in _pages(api, "/repos/%s/pulls?state=all&sort=created&direction=desc" % repo):
        head = pr.get("head") or {}
        if (head.get("repo") or {}).get("full_name") != repo:
            continue  # fork PRs: their branches are not ours
        prs.setdefault(head.get("ref"), []).append(pr)
    out = []
    for b in branches:
        name, sha = b["name"], b["commit"]["sha"]
        if name in keep or b.get("protected"):
            continue
        n = _lock_issue(name)
        if n is not None:
            issue = api("GET", "/repos/%s/issues/%d" % (repo, n))
            labels = {lb["name"] for lb in issue.get("labels", [])}
            if issue.get("state") == "open" and "status:doing" in labels:
                continue
        mine = prs.get(name, [])
        if mine:
            if any(p["state"] == "open" for p in mine):
                continue
            latest = max(mine, key=lambda p: p["created_at"])
            if latest["head"]["sha"] == sha:
                out.append((name, sha, "PR #%d %s" % (latest["number"], "merged" if latest.get("merged_at") else "closed")))
            continue
        cmp = api("GET", "/repos/%s/compare/dev...%s" % (repo, _ref(name)))
        if cmp.get("ahead_by", 1) != 0:
            continue
        c = api("GET", "/repos/%s/commits/%s" % (repo, sha))
        when = datetime.fromisoformat(c["commit"]["committer"]["date"].replace("Z", "+00:00"))
        if now - when > timedelta(days=days):
            out.append((name, sha, "no PR, nothing beyond dev, tip %s" % when.date().isoformat()))
    return out


def run(api, repo: str, dry_run: bool, keep=(), days: int = 7, now=None) -> list[str]:
    """Deletes (or, with dry_run, only lists) the planned branches; returns summary lines."""
    lines = []
    for name, sha, why in plan(api, repo, keep, days, now):
        if dry_run:
            lines.append("would delete `%s` %s (%s)" % (name, sha[:12], why))
            continue
        api("DELETE", "/repos/%s/git/refs/heads/%s" % (repo, _ref(name)))
        lines.append("deleted `%s` %s (%s)" % (name, sha[:12], why))
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep", default="", help="comma-separated branch names never to delete")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    a = ap.parse_args(argv)
    keep = [k.strip() for k in a.keep.split(",") if k.strip()]
    lines = run(http_api, a.repo, a.dry_run, keep, a.days)
    head = "## stale branches (%s): %d\n\n" % ("dry run" if a.dry_run else "deleted", len(lines))
    text = head + "".join("- %s\n" % ln for ln in lines)
    print(text)
    if a.summary:
        with open(a.summary, "a", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
