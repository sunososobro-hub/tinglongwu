#!/usr/bin/env python3
"""#956 (part 1 of #950): decide what a hotfix ships before any workflow builds it.

    python scripts/hotfix_plan.py plan --fix <sha|PR number> [--repo OWNER/REPO]
    python scripts/hotfix_plan.py apply --fix <sha> --tag vX.Y.Z      (#957, in a checkout of the base tag)
    python scripts/hotfix_plan.py summary --gate NAME=RESULT ... --started ISO [--uploaded ISO]   (#957 job summary)

Prints `tag=vX.Y.(Z+1)`, `base=<latest release tag>`, `fix=<commit sha>`, `issue=<N>` (also appended to $GITHUB_OUTPUT
when set), or exits 1 with the reason. Rules:
  - the fix is a merged PR number (its merge commit, base dev) or a commit sha that is an ancestor of dev;
  - the linked issue (`Closes/Fixes/Refs #N` in the PR body) has the labels `bug` and `P0` or `P1`;
  - no cadence release is pending: dev's `__version__` already has its tag, and no `release: bump __version__` PR is open;
  - tag = latest published (non-draft, non-pre-release) release tag with patch + 1, and that tag must not exist yet.
`apply` (#957, part 2) cherry-picks the fix onto the checked-out base tag and sets `__version__` to the tag in one
more commit; it prints `head=<sha>` and `tree=<sha>` (also to $GITHUB_OUTPUT) or exits 1 when the pick conflicts, is
empty (the fix is already in the base), the base predates the layered core (#923; v0.3.5 does) or the
worktree has uncommitted tracked changes, or the version line is missing. Local git only, nothing is pushed.
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
from datetime import datetime

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


# who the hotfix commits are by (local commits only; the runner has no git identity, #944)
GIT_IDENTITY = ("-c", "user.name=github-actions[bot]",
                "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com")
INIT_PY = "tinglongwu/__init__.py"
# #923 layered core: the upgrade overlay and wizard checks need its runtime-layer.json, which older bases never build
LAYERED_MARKER = "packaging/runtime_layer.py"


def _git(*args: str, cwd: str = ".", check: bool = True) -> str:
    out = subprocess.run(["git", *GIT_IDENTITY, *args], cwd=cwd, capture_output=True, text=True)
    if check and out.returncode:
        raise PlanError(f"git {' '.join(args)} failed: {(out.stderr or out.stdout).strip()}")
    return out.stdout.strip()


def apply(fix: str, tag: str, cwd: str = ".") -> dict:
    """Cherry-pick `fix` onto HEAD (the base tag) and set __version__ to `tag`. Returns {head, tree}."""
    if not TAG_RE.match(tag or ""):
        raise PlanError(f"tag {tag!r} is not vX.Y.Z")
    sha = _git("rev-parse", "--verify", f"{fix}^{{commit}}", cwd=cwd)
    base = _git("rev-parse", "HEAD", cwd=cwd)
    if _git("status", "--porcelain", "--untracked-files=no", cwd=cwd):
        raise PlanError("the worktree has uncommitted changes; commit or stash them before apply")
    if not os.path.isfile(os.path.join(cwd, LAYERED_MARKER)):
        raise PlanError(f"base {base[:7]} predates the layered core (#923, no {LAYERED_MARKER}): "
                        "hotfixes start from the first release that has it")
    parents = _git("rev-list", "--parents", "-n", "1", sha, cwd=cwd).split()[1:]
    pick = ["cherry-pick", "-x"] + (["-m", "1"] if len(parents) > 1 else []) + [sha]
    out = subprocess.run(["git", *GIT_IDENTITY, *pick], cwd=cwd, capture_output=True, text=True)
    if out.returncode:
        conflicts = _git("diff", "--name-only", "--diff-filter=U", cwd=cwd, check=False).split()
        _git("cherry-pick", "--abort", cwd=cwd, check=False)
        _git("reset", "--hard", base, cwd=cwd, check=False)
        if conflicts:
            raise PlanError(f"cherry-pick of {sha[:7]} onto {base[:7]} conflicts in: {', '.join(conflicts)}")
        if "empty" in (out.stdout + out.stderr):
            raise PlanError(f"commit {sha[:7]} changes nothing on {base[:7]} (already in the base?)")
        raise PlanError(f"cherry-pick of {sha[:7]} failed: {(out.stderr or out.stdout).strip()}")
    path = os.path.join(cwd, INIT_PY)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new, n = VERSION_RE.subn(f'__version__ = "{tag[1:]}"', text, count=1)
    if not n:
        _git("reset", "--hard", base, cwd=cwd, check=False)
        raise PlanError(f"no __version__ line in {INIT_PY}")
    if new != text:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        _git("commit", "-q", "-m", f"hotfix {tag}: __version__ = {tag[1:]}", "--", INIT_PY, cwd=cwd)
    return {"head": _git("rev-parse", "HEAD", cwd=cwd), "tree": _git("rev-parse", "HEAD^{tree}", cwd=cwd)}


HOTFIX_BUDGET_MIN = 20  # #957 Acceptance: dispatch -> tested artifacts uploaded
PASSED = ("success",)


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def summary(gates, started: str, uploaded: str = "", budget_min: int = HOTFIX_BUDGET_MIN) -> tuple[str, bool]:
    """Markdown for the hotfix job summary: one row per gate (pass/fail) and the dispatch -> upload wall time.

    `gates` = [(name, result)] with GitHub job/step results (success, failure, cancelled, skipped, '').
    Returns (markdown, all gates passed and the artifact was uploaded)."""
    rows = ["| gate | result |", "| --- | --- |"]
    ok = bool(gates)
    for name, result in gates:
        passed = result in PASSED
        ok &= passed
        rows.append(f"| {name} | {'pass' if passed else 'FAIL'} ({result or 'not run'}) |")
    if uploaded:
        minutes = (_ts(uploaded) - _ts(started)).total_seconds() / 60
        over = minutes > budget_min
        line = f"hotfix wall time (dispatch -> artifacts uploaded): {minutes:.1f} min (budget {budget_min} min)"
        if over:
            line += " - OVER BUDGET"
    else:
        ok = False
        line = "hotfix wall time: no artifact uploaded (a gate failed)"
    return "### Hotfix dry run gates\n\n" + "\n".join(rows) + f"\n\n{line}\n", ok


def _emit(res: dict, keys) -> None:
    lines = "".join(f"{k}={res[k]}\n" for k in keys)
    print(lines, end="")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(lines)


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
    q = sub.add_parser("apply", help="cherry-pick the fix onto the checked-out base and set __version__ (#957)")
    q.add_argument("--fix", required=True, help="commit sha (the plan's fix=)")
    q.add_argument("--tag", required=True, help="the plan's tag=")
    r = sub.add_parser("summary", help="gate table + wall time for the job summary (#957)")
    r.add_argument("--gate", action="append", default=[], help="NAME=RESULT, in gate order")
    r.add_argument("--started", required=True, help="run created_at (ISO 8601)")
    r.add_argument("--uploaded", default="", help="when the tested artifact was uploaded (ISO 8601); empty = not")
    a = ap.parse_args(argv)
    if a.cmd == "summary":
        md, ok = summary([tuple(g.split("=", 1)) if "=" in g else (g, "") for g in a.gate], a.started, a.uploaded)
        print(md, end="")
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                f.write(md)
        return 0 if ok else 1
    if a.cmd == "apply":
        try:
            _emit(apply(a.fix, a.tag), ("head", "tree"))
        except PlanError as e:
            print(f"hotfix_plan: {e}", file=sys.stderr)
            return 1
        return 0
    if not a.repo:
        ap.error("--repo (or GITHUB_REPOSITORY) is required")
    try:
        res = plan(a.fix, _gh_api(a.repo))
    except (PlanError, NotFound) as e:
        print(f"hotfix_plan: {e}", file=sys.stderr)
        return 1
    _emit(res, ("tag", "base", "fix", "issue"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
