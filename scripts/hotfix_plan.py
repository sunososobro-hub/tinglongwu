#!/usr/bin/env python3
"""#956 (part 1 of #950): decide what a hotfix ships before any workflow builds it.

    python scripts/hotfix_plan.py plan --fix <sha|PR number> [--repo OWNER/REPO]

Prints `tag=vX.Y.(Z+1)`, `base=<latest release tag>`, `fix=<commit sha>`, `issue=<N>` (also appended to $GITHUB_OUTPUT
when set), or exits 1 with the reason. Rules:
  - the fix is a merged PR number (its merge commit, base dev) or a commit sha that is an ancestor of dev;
  - the linked issue (`Closes/Fixes/Refs #N` in the PR body) has the labels `bug` and `P0` or `P1`;
  - no cadence release is pending: dev's `__version__` already has its tag, and no `release: bump __version__` PR is open;
  - tag = latest published (non-draft, non-pre-release) release tag with patch + 1, and that tag must not exist yet.
Network only through the `gh` CLI (GH_TOKEN). `plan()` takes the API as a function, so tests stub it.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys

BRANCH = "dev"
PER_PAGE = 100
URGENT = ("P0", "P1")
LINK_RE = re.compile(r"\b(?:closes|fixes|resolves|refs)\s*:?\s*#(\d+)", re.I)
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.M)  # same as release_notes.py
BUMP_PREFIX = "release: bump __version__"


class PlanError(Exception):
    """The hotfix is not allowed; the message is the reason."""


class NotFound(Exception):
    """The API answered 404."""


def next_tag(latest: str) -> str:
    m = TAG_RE.match(latest or "")
    if not m:
        raise PlanError(f"latest release tag {latest!r} is not vX.Y.Z")
    return f"v{m[1]}.{m[2]}.{int(m[3]) + 1}"


def linked_issue(body: str) -> int:
    m = LINK_RE.search(body or "")
    if not m:
        raise PlanError("the fix PR body has no Closes/Refs #N")
    return int(m[1])


def _tag_exists(tag: str, api) -> bool:
    try:
        api(f"git/ref/tags/{tag}")
    except NotFound:
        return False
    return True


def check_no_pending_release(api) -> None:
    """Refuse while a cadence release is on its way: dev bumped but untagged (covers a release freeze), or a bump PR open."""
    # Open PRs first: a bump that merges between the two reads is then caught by the version check (dev untagged).
    page = 1
    while True:
        prs = api(f"pulls?state=open&per_page={PER_PAGE}&page={page}")
        for pr in prs:
            if (pr.get("title") or "").startswith(BUMP_PREFIX):
                raise PlanError(f"bump PR #{pr.get('number')} is open")
        if len(prs) < PER_PAGE:
            break
        page += 1
    content = api(f"contents/tinglongwu/__init__.py?ref={BRANCH}").get("content", "")
    m = VERSION_RE.search(base64.b64decode(content).decode("utf-8", "replace"))
    if not m:
        raise PlanError(f"cannot read __version__ from tinglongwu/__init__.py on {BRANCH}")
    version = m[1]
    if not _tag_exists(f"v{version}", api):
        raise PlanError(f"{BRANCH} __version__ {version} is bumped but not released; wait for that release")


def plan(fix: str, api) -> dict:
    """`api(path) -> parsed JSON` (raises NotFound on 404). Returns {tag, base, fix, issue} or raises PlanError."""
    fix = str(fix).strip().lstrip("#")
    if not fix:
        raise PlanError("--fix is empty")
    if fix.isdigit():
        pr = api(f"pulls/{fix}")
        if not pr.get("merged"):
            raise PlanError(f"PR #{fix} is not merged")
        if (pr.get("base") or {}).get("ref") != BRANCH:
            raise PlanError(f"PR #{fix} was not merged into {BRANCH}")
        sha = pr.get("merge_commit_sha") or ""
    else:
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", fix):
            raise PlanError(f"{fix!r} is neither a PR number nor a commit sha")
        try:
            sha = api(f"commits/{fix}")["sha"]
        except NotFound:
            raise PlanError(f"commit {fix} does not exist") from None
        if api(f"compare/{sha}...{BRANCH}").get("status") not in ("ahead", "identical"):
            raise PlanError(f"commit {sha[:7]} is not an ancestor of {BRANCH}")
        merged = [p for p in api(f"commits/{sha}/pulls") if p.get("merged_at")]
        if not merged:
            raise PlanError(f"commit {sha[:7]} has no merged PR to read the issue link from")
        pr = merged[0]
    issue = linked_issue(pr.get("body", ""))
    labels = {x["name"] for x in api(f"issues/{issue}").get("labels", [])}
    if "bug" not in labels:
        raise PlanError(f"issue #{issue} is not labelled bug")
    if not labels & set(URGENT):
        raise PlanError(f"issue #{issue} is not P0/P1")
    check_no_pending_release(api)
    base = api("releases/latest").get("tag_name", "")
    tag = next_tag(base)
    if _tag_exists(tag, api):
        raise PlanError(f"tag {tag} already exists")
    return {"tag": tag, "base": base, "fix": sha, "issue": issue}


def _gh_api(repo: str):
    def api(path: str):
        out = subprocess.run(["gh", "api", f"repos/{repo}/{path}"], capture_output=True, text=True)
        if out.returncode:
            if "404" in out.stderr or "Not Found" in out.stderr:
                raise NotFound(path)
            raise PlanError(f"gh api {path} failed: {out.stderr.strip()}")
        return json.loads(out.stdout)
    return api


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--fix", required=True, help="merged PR number or commit sha on dev")
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="OWNER/REPO")
    a = ap.parse_args(argv)
    if not a.repo:
        ap.error("--repo (or GITHUB_REPOSITORY) is required")
    try:
        res = plan(a.fix, _gh_api(a.repo))
    except (PlanError, NotFound) as e:
        print(f"hotfix_plan: {e}", file=sys.stderr)
        return 1
    lines = "".join(f"{k}={res[k]}\n" for k in ("tag", "base", "fix", "issue"))
    print(lines, end="")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
