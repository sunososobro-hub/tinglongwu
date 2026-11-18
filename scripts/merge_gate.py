"""#1473: rule-only merge gate for PRs into `dev` (rules sections 2, 3, 14, 17), run by an Action.

The fixed-rule part of R-merge / S-merge: for every open PR into `dev` it decides one of

  merge       all rule gates pass, no protected / g4 path (or an Opus PASS newer than the head, or an exempt patch
              bump) and no unresolved review thread: squash-merge with the checked head SHA;
  needs-opus  a protected / g4 path (section 14) or a head that is not fix/N (section 2) without an Opus PASS:
              add the `needs-opus` label for the Opus review queue;
  needs-ai    a judgement step is left (unresolved review-bot or human thread, a dirty branch, a paperwork gap in the
              body, a hotfix back-merge): add `needs-review-ai` so the AI mergers take it;
  wait        `test` not finished, mergeability unknown: do nothing, the next event re-runs the gate;
  hold        a hard stop (section 3 / 14) or a FAIL / CHANGES not yet answered by a newer commit: do nothing.

Usage: python scripts/merge_gate.py [--pr N] [--apply]   (needs `gh` with GH_TOKEN; default is a dry run)
It never checks out or runs PR code: it reads the PR through the REST / GraphQL API only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = "sunososobro-hub/tinglongwu"
ALLOWED_REVIEWERS = frozenset({"sunososobro-hub"})  # section 14 "Allowed review authors"
NEEDS_OPUS, NEEDS_AI = "needs-opus", "needs-review-ai"

RELEASE_CRITICAL = ("ci.yml", "e2e.yml", "windows-build.yml", "hw-tests.yml", "release.yml", "release-pr.yml",
                    "hotfix.yml")
PROTECTED_EXACT = {".agent/tlwu_rules.md", "scripts/release_notes.py", "scripts/release_check.py",
                   "tinglongwu/__init__.py", "pyproject.toml", "tinglongwu/offline.py", "scripts/download_models.py",
                   "scripts/make_model_pins.py"}
PROTECTED_PREFIX = (".claude/", "packaging/", "installer/")

HEAD_RE = re.compile(r"^(?:fix/(\d+)(?:-r\d+)?|feat/(\d+))$")
LINK_RE = re.compile(r"\b(?:Closes|Refs)\s+#(\d+)\b", re.I)
USAGE_RE = re.compile(r"^tlwu-usage:.*\bminutes=\d+\s*$")
STOP_ISSUE_LABELS = {"status:frozen", "status:proposal", "needs-owner"}
PASS_RE = re.compile(r"^\s*Opus review: (?:PASS|APPROVE)\b", re.M)
REJECT_RE = re.compile(r"(?:AI review: FAIL|Opus review: CHANGES)")
STOP_RE = re.compile(r"(?<!non-)(?<!not )\b(?:needs owner|blocking)\b", re.I)
BOT_BLOCK_RE = re.compile(r"<!-- This is an auto-generated comment.*?<!-- end of auto-generated comment[^>]*-->",
                          re.S)


def is_protected(path: str) -> bool:
    """Section 14 protected paths plus the g4 sensitive paths; files under tests/ never count (section 3)."""
    if path.startswith("tests/"):
        return False
    if path in PROTECTED_EXACT or path.startswith(PROTECTED_PREFIX):
        return True
    if re.fullmatch(r"requirements[^/]*\.txt", path) or re.fullmatch(r"scripts/[^/]*offline[^/]*", path):
        return True
    if path.startswith(".github/workflows/"):
        name = path.rsplit("/", 1)[-1]
        return name in RELEASE_CRITICAL or name.startswith("auto-release")
    return False


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def clean_body(body: str | None) -> str:
    """The PR body without review-bot blocks (CodeRabbit appends its summary after the usage line)."""
    return BOT_BLOCK_RE.sub("", body or "")


def usage_ok(body: str | None) -> bool:
    return any(USAGE_RE.match(x.strip()) for x in clean_body(body).splitlines())


def linked_issue(body: str | None) -> int | None:
    m = LINK_RE.search(clean_body(body))
    return int(m.group(1)) if m else None


def opus_pass_after(notes: list[dict], head_at: datetime) -> datetime | None:
    """Newest `Opus review: PASS` by an allowed author posted after the head commit (section 14)."""
    times = [_ts(n["at"]) for n in notes
             if n.get("author") in ALLOWED_REVIEWERS and PASS_RE.search(n.get("body") or "") and _ts(n["at"]) > head_at]
    return max(times) if times else None


def evaluate(pr: dict, files: list[dict], checks: list[dict], issue: dict | None, notes: list[dict],
             unresolved_threads: int, head_at: datetime, bump=None, legacy_freeze: bool = False) -> tuple[str, str]:
    """Pure decision for one PR. Returns (action, reason).

    pr: GET pulls/N (base.ref, head.ref, head.repo.full_name, draft, mergeable_state, changed_files, body, labels).
    checks: check runs on the head commit. issue: GET issues/<linked> or None. notes: PR comments and reviews as
    {author, body, at}. head_at: committer date of the head commit. bump: callable(files) -> (exempt, reason),
    release_notes.bump_exempt. legacy_freeze: a legacy dev -> main release PR is open (section 14 Transition)."""
    head = pr["head"]["ref"]
    body = pr.get("body") or ""
    if pr["base"]["ref"] != "dev":
        return "skip", "base is not dev"
    if pr.get("draft"):
        return "skip", "draft"
    if (pr["head"].get("repo") or {}).get("full_name") != REPO:
        return "skip", "fork PR: AI mergers run the Opus review (section 14 Outside)"
    if head.startswith("release/"):
        return "skip", "release snapshot"
    if head.startswith("hotfix/"):
        return "needs-ai", "hotfix back-merge: merge commit, AI merger"
    if not HEAD_RE.match(head):
        return "needs-opus", "head is not fix/N; reopen as fix/<issue>"
    if legacy_freeze and "release-fix" not in body:
        return "hold", "legacy release freeze: body lacks release-fix"

    pass_at = opus_pass_after(notes, head_at)
    for n in notes:
        if STOP_RE.search(n.get("body") or "") and not (pass_at and pass_at > _ts(n["at"])):
            if n.get("author", "").endswith("[bot]"):
                continue  # review bots' wording is not an owner stop; their threads count below
            return "hold", "unresolved needs owner / blocking"
    rejects = [_ts(n["at"]) for n in notes if REJECT_RE.search(n.get("body") or "")]
    if rejects and max(rejects) > head_at:
        return "hold", "FAIL / CHANGES not answered by a newer commit"

    n = linked_issue(body)
    if n is None:
        return "needs-ai", "paperwork: no Closes/Refs line"
    if issue is None:
        return "hold", f"linked issue #{n} not found"
    if issue.get("pull_request"):
        return "hold", f"#{n} is a PR, not an issue"
    ilabels = {lb["name"] for lb in issue.get("labels", [])}
    if ilabels & STOP_ISSUE_LABELS:
        return "hold", f"issue #{n} has {', '.join(sorted(ilabels & STOP_ISSUE_LABELS))}"
    if not usage_ok(body):
        return "needs-ai", "paperwork: no tlwu-usage line"

    if len(files) != int(pr.get("changed_files", -1)):
        return "wait", f"file list has {len(files)} files, PR reports {pr.get('changed_files')}"
    protected = sorted(f["filename"] for f in files
                       if is_protected(f["filename"]) or is_protected(f.get("previous_filename") or ""))
    if protected:
        exempt = bump is not None and bump(files)[0]
        if not exempt and not pass_at:
            return "needs-opus", "protected / g4 path: " + ", ".join(protected[:5])
    if any(f["filename"] == "tinglongwu/__init__.py" for f in files) and not (bump and bump(files)[0]) and not pass_at:
        return "needs-opus", "touches tinglongwu/__init__.py"

    test = [c for c in checks if c.get("name") == "test"]
    if not test or any(c.get("status") != "completed" for c in test):
        return "wait", "test not finished"
    if any(c.get("conclusion") != "success" for c in test):
        return "hold", "test is red"
    state = pr.get("mergeable_state")
    if state == "dirty":
        return "needs-ai", "merge conflict: resolve in place (section 7)"
    if state not in ("clean", "unstable", "has_hooks"):
        return "wait", f"mergeable_state is {state}"
    if unresolved_threads:
        return "needs-ai", f"{unresolved_threads} unresolved review thread(s)"
    return "merge", "all rule gates pass"


# ---------------------------------------------------------------- I/O (gh only)

def _gh(path: str, *args: str):
    out = subprocess.run(["gh", "api", path, *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else None


def _gh_list(path: str) -> list:
    pages = _gh(path, "--paginate", "--slurp") or []
    return [x for page in pages for x in (page if isinstance(page, list) else [page])]


def _release_notes():
    spec = importlib.util.spec_from_file_location("release_notes", Path(__file__).with_name("release_notes.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _unresolved(num: int) -> int:
    owner, name = REPO.split("/")
    q = ("query($o:String!,$n:String!,$p:Int!){repository(owner:$o,name:$n){pullRequest(number:$p)"
         "{reviewThreads(first:100){nodes{isResolved isOutdated}}}}}")
    data = _gh("graphql", "-f", f"query={q}", "-f", f"o={owner}", "-f", f"n={name}", "-F", f"p={num}")
    nodes = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    return sum(1 for t in nodes if not t["isResolved"] and not t["isOutdated"])


def gather(num: int, rn) -> tuple:
    pr = _gh(f"repos/{REPO}/pulls/{num}")
    sha = pr["head"]["sha"]
    files = _gh_list(f"repos/{REPO}/pulls/{num}/files?per_page=100")
    checks = [c for p in (_gh(f"repos/{REPO}/commits/{sha}/check-runs?per_page=100", "--paginate", "--slurp") or [])
              for c in p.get("check_runs", [])]
    head_at = _ts(_gh(f"repos/{REPO}/commits/{sha}")["commit"]["committer"]["date"])
    notes = [{"author": c["user"]["login"], "body": c.get("body"), "at": c["created_at"]}
             for c in _gh_list(f"repos/{REPO}/issues/{num}/comments?per_page=100")]
    notes += [{"author": r["user"]["login"], "body": r.get("body"), "at": r["submitted_at"]}
              for r in _gh_list(f"repos/{REPO}/pulls/{num}/reviews?per_page=100") if r.get("submitted_at")]
    n = linked_issue(pr.get("body"))
    m = HEAD_RE.match(pr["head"]["ref"])
    n = n or (int(m.group(1) or m.group(2)) if m else None)
    issue = None
    if n:
        try:
            issue = _gh(f"repos/{REPO}/issues/{n}")
        except subprocess.CalledProcessError:
            issue = None
    if _gh(f"repos/{REPO}/pulls/{num}")["head"]["sha"] != sha:
        raise RuntimeError("head moved while reading")
    wn = None
    if any(f["filename"] == rn.WHATS_NEW_FILE for f in files):
        wn = subprocess.run(["gh", "api", f"repos/{REPO}/contents/{rn.WHATS_NEW_FILE}?ref={sha}", "-H",
                             "Accept: application/vnd.github.raw"], check=True, capture_output=True, text=True).stdout
    return pr, files, checks, issue, notes, _unresolved(num), head_at, (lambda fs: rn.bump_exempt(fs, wn))


def legacy_freeze() -> bool:
    pulls = _gh_list(f"repos/{REPO}/pulls?state=open&base=main&per_page=100")
    return any(p["head"]["ref"] == "dev" and p["title"].startswith("release:") for p in pulls)


def apply(num: int, pr: dict, action: str, reason: str) -> None:
    labels = {lb["name"] for lb in pr.get("labels", [])}
    if action == "merge":
        subprocess.run(["gh", "api", "-X", "PUT", f"repos/{REPO}/pulls/{num}/merge", "-f", "merge_method=squash",
                        "-f", f"sha={pr['head']['sha']}"], check=True)
    elif action in ("needs-opus", "needs-ai"):
        label = NEEDS_OPUS if action == "needs-opus" else NEEDS_AI
        if label not in labels:
            subprocess.run(["gh", "api", "-X", "POST", f"repos/{REPO}/issues/{num}/labels", "-f", f"labels[]={label}"],
                           check=True, capture_output=True)
            subprocess.run(["gh", "api", "-X", "POST", f"repos/{REPO}/issues/{num}/comments", "-f",
                            f"body=merge-gate: {label}: {reason}"], check=True, capture_output=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", type=int, action="append", help="only these PRs (default: every open PR into dev)")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    rn = _release_notes()
    nums = a.pr or [p["number"] for p in _gh_list(f"repos/{REPO}/pulls?state=open&base=dev&per_page=100")]
    freeze = legacy_freeze()
    rc = 0
    for num in nums:
        try:
            pr, files, checks, issue, notes, threads, head_at, bump = gather(num, rn)
            action, reason = evaluate(pr, files, checks, issue, notes, threads, head_at, bump, freeze)
            print(f"#{num} {action}: {reason}")
            if a.apply:
                apply(num, pr, action, reason)
        except Exception as e:  # one bad PR never stops the rest
            print(f"#{num} error: {e}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
