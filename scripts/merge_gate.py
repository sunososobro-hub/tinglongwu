"""#1473: rule-only merge gate for PRs into `dev` (rules sections 2, 3, 14, 17), run by an Action.

The fixed-rule part of R-merge / S-merge: for every open PR into `dev` it decides one of

  merge       all rule gates pass, no protected / g4 path (or an Opus PASS newer than the head, a PASS on an older
              head with no protected change since (#1580), or an exempt patch
              bump) and no unresolved review thread: squash-merge with the checked head SHA; #1885: when the only
              unresolved threads are review-bot Minor / Trivial / Nitpick findings, it replies one line to each,
              resolves it, logs it on the rolling `[review] minor findings` issue, then merges (rules section 3
              "One review round" run by a script);
  needs-opus  a protected / g4 path (section 14) or a head that is not fix/N (section 2) without an Opus PASS:
              add the `needs-opus` label for the Opus review queue;
  needs-ai    a judgement step is left (a human thread, a review-bot Critical / Major (or unrated) thread, a dirty
              branch, a paperwork gap in the
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
MOBILE_CHECK = "mobile-apk"  # #1618: workflow name; its job is a generic `build`, so gather() reads the workflow run
EVAL_CHECK = "mini-eval"  # #1687: eval.yml job (check run) name; needed only when a file matches EVAL_PATH_RE
# #1687: keep in step with eval.yml's "Does this PR touch translation / ASR / eval code?" filter. Post-#1671
# layout: src/{core,ui,flow,system}/tinglongwu/, eval/ (incl. eval/tlwu_eval/), src/system/tinglongwu_setup/.
EVAL_PATH_RE = re.compile(r"^(?:src/[^/]+/tinglongwu/|eval/|src/system/tinglongwu_setup/manifest\.json$"
                          r"|scripts/bench/make_ci_smoke\.py$|\.github/workflows/eval[^/]*\.yml$)")

RELEASE_CRITICAL = ("ci.yml", "e2e.yml", "windows-build.yml", "hw-tests.yml", "release.yml", "release-pr.yml",
                    "hotfix.yml")
PROTECTED_EXACT = {".agent/tlwu_rules.md", "scripts/release_notes.py", "scripts/release_check.py",
                   "src/core/tinglongwu/__init__.py", "pyproject.toml", "src/system/tinglongwu/offline.py", "scripts/download_models.py",
                   "scripts/make_model_pins.py", "scripts/merge_gate.py", ".github/workflows/merge-gate.yml"}
PROTECTED_PREFIX = (".agent/", ".claude/", "release/packaging/", "release/installer/")  # #1856: every .agent rules file

HEAD_RE = re.compile(r"^(?:fix/(\d+)(?:-r\d+)?|feat/(\d+))$")
LINK_RE = re.compile(r"\b(?:Closes|Refs)\s+#(\d+)\b", re.I)
USAGE_RE = re.compile(r"^tlwu-usage:.*\bminutes=\d+\s*$")
STOP_ISSUE_LABELS = {"status:frozen", "status:proposal", "needs-owner"}
# Section 14: a protected path merges only when its linked issue was opened by the owner or a collaborator; a PASS
# never overrides it. github-actions[bot] is this repo's own workflows (e.g. bump-action.yml's bump issues): only
# code already on a branch of this repo can post as it, so it counts as a collaborator.
TRUSTED_ASSOC = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
TRUSTED_ISSUE_BOTS = frozenset({"github-actions[bot]"})
PASS_RE = re.compile(r"^\s*Opus review: (?:PASS|APPROVE)\b", re.M)
REJECT_RE = re.compile(r"(?:AI review: FAIL|Opus review: CHANGES)")
STOP_RE = re.compile(r"(?<!non-)(?<!not )\b(?:needs owner|blocking)\b", re.I)
# #1885: review-bot severity markers. CodeRabbit writes `<img ...></picture> Minor** · ...` (older: `_🟡 Minor_`).
# Only these count as minor; Critical / Major or a thread with no marker goes to an AI merger (fail closed).
SEVERITY_RE = re.compile(r"(?:</picture>\s*|_\S{1,3}\s+)(Critical|Major|Minor|Trivial|Nitpick|Info)(?![A-Za-z])")
MINOR_SEVERITIES = frozenset({"Minor", "Trivial", "Nitpick", "Info"})
MINOR_LOG_TITLE = "[review] minor findings"
BOT_BLOCK_RE = re.compile(r"<!-- This is an auto-generated comment.*?<!-- end of auto-generated comment[^>]*-->",
                          re.S)


def is_protected(path: str) -> bool:
    """Section 14 protected paths plus the g4 sensitive paths; files under tests/ never count (section 3)."""
    if path.startswith("tests/"):
        return False
    if path in PROTECTED_EXACT or path.startswith(PROTECTED_PREFIX):
        return True
    if re.fullmatch(r"requirements/[^/]*\.txt", path) or re.fullmatch(r"scripts/[^/]*offline[^/]*", path):
        return True
    if path.startswith(".github/workflows/"):
        name = path.rsplit("/", 1)[-1]
        return name in RELEASE_CRITICAL or name.startswith("auto-release")
    return False


def trusted_issue(issue: dict) -> bool:
    """Linked issue opened by the owner / a collaborator (author_association) or by this repo's own workflows."""
    if issue.get("author_association") in TRUSTED_ASSOC:
        return True
    return (issue.get("user") or {}).get("login") in TRUSTED_ISSUE_BOTS


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


def opus_pass_after(notes: list[dict], head_at: datetime, sha: str) -> datetime | None:
    """Newest `Opus review: PASS` by an allowed author posted after the head commit (section 14) and bound to the
    head SHA: a review submitted on that commit (`commit_id`) or a comment naming its first 7+ characters. A committer
    date can be set freely, so the time alone never binds a PASS to a later push; an unbound PASS is left to the AI
    mergers (fail closed)."""
    def bound(n):
        if n.get("commit_id") == sha:
            return True
        line = next((x for x in (n.get("body") or "").splitlines() if PASS_RE.match(x)), "")
        first = re.search(r"\b[0-9a-f]{7,40}\b", line)  # the approved SHA: the first one on the PASS line only
        return bool(sha) and bool(first) and sha.startswith(first.group(0))
    times = [_ts(n["at"]) for n in notes
             if n.get("author") in ALLOWED_REVIEWERS and PASS_RE.search(n.get("body") or "") and _ts(n["at"]) > head_at
             and bound(n)]
    return max(times) if times else None


def approved_sha(n: dict) -> str:
    """The SHA an `Opus review: PASS` approves: the review's commit, else the first SHA on the PASS line."""
    if n.get("commit_id"):
        return n["commit_id"]
    line = next((x for x in (n.get("body") or "").splitlines() if PASS_RE.match(x)), "")
    first = re.search(r"\b[0-9a-f]{7,40}\b", line)
    return first.group(0) if first else ""


def carried_pass(notes: list[dict], carry) -> datetime | None:
    """#1580: newest allowed-author PASS on an OLDER head that still holds: `carry(sha)` says that SHA is an ancestor
    of the PR head and no protected / g4 path changed between it and the head, so the protected content Opus approved
    is exactly what would merge. A later push that only touches ordinary files then needs no second Opus review."""
    if carry is None:
        return None
    # Only a full 40-character SHA carries: a short one could be redirected by a same-named branch or tag, or a
    # commit with the same prefix. A PASS older than any later FAIL / CHANGES / needs owner / blocking never carries.
    stops = [_ts(n["at"]) for n in notes
             if REJECT_RE.search(n.get("body") or "")
             or (STOP_RE.search(n.get("body") or "") and not n.get("author", "").endswith("[bot]"))]
    last_stop = max(stops) if stops else None
    times = []
    for n in notes:
        if n.get("author") in ALLOWED_REVIEWERS and PASS_RE.search(n.get("body") or ""):
            sha = approved_sha(n)
            at = _ts(n["at"])
            if last_stop and at <= last_stop:
                continue
            if re.fullmatch(r"[0-9a-f]{40}", sha) and carry(sha):
                times.append(at)
    return max(times) if times else None


def minor_bot_thread(t: dict) -> bool:
    """#1885: an unresolved thread a review bot opened with a Minor / Trivial / Nitpick / Info marker."""
    if not (t.get("author") or "").endswith("[bot]") or t.get("human_reply"):
        return False  # a person's thread, or a person answered in it (e.g. "blocking"): an AI merger decides
    m = SEVERITY_RE.search(t.get("body") or "")
    return bool(m) and m.group(1) in MINOR_SEVERITIES


def evaluate(pr: dict, files: list[dict], checks: list[dict], issue: dict | None, notes: list[dict],
             unresolved_threads, head_at: datetime, bump=None, legacy_freeze: bool = False,
             carry=None) -> tuple[str, str]:
    """Pure decision for one PR. Returns (action, reason).

    pr: GET pulls/N (base.ref, head.ref, head.repo.full_name, draft, mergeable_state, changed_files, body, labels).
    checks: check runs on the head commit. issue: GET issues/<linked> or None. notes: PR comments and reviews as
    {author, body, at}. head_at: committer date of the head commit. bump: callable(files) -> (exempt, reason),
    release_notes.bump_exempt. legacy_freeze: a legacy dev -> main release PR is open (section 14 Transition).
    carry: callable(sha) -> bool for #1580 (see carried_pass); None = only a PASS bound to the head counts.
    unresolved_threads: a count (every thread needs an AI merger) or, #1885, a list of thread dicts
    {id, author, body, path, url, comment_id}: review-bot Minor threads alone do not stop a merge."""
    head = pr["head"]["ref"]
    body = pr.get("body") or ""
    if pr.get("state", "open") != "open":
        return "skip", "not open"
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

    pass_at = opus_pass_after(notes, head_at, pr["head"].get("sha", "")) or carried_pass(notes, carry)
    for n in notes:
        if STOP_RE.search(n.get("body") or "") and not (pass_at and pass_at > _ts(n["at"])):
            if n.get("author", "").endswith("[bot]"):
                continue  # review bots' wording is not an owner stop; their threads count below
            return "hold", "unresolved needs owner / blocking"
    plabels = {lb["name"] for lb in pr.get("labels", [])}
    if "needs-owner" in plabels:
        return "hold", "PR has needs-owner"  # section 3 / 14 hard stop; a PASS does not lift an owner question
    if NEEDS_OPUS in plabels and not pass_at:
        return "hold", "PR has needs-opus and no Opus PASS on its head"  # e.g. section 3 "failed twice"
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
    if protected and not trusted_issue(issue):
        return "hold", f"protected path but issue #{n} was not opened by a collaborator (section 14)"
    if protected:
        exempt = bump is not None and bump(files)[0]
        if not exempt and not pass_at:
            return "needs-opus", "protected / g4 path: " + ", ".join(protected[:5])
    if any(f["filename"] == "src/core/tinglongwu/__init__.py" for f in files) and not (bump and bump(files)[0]) and not pass_at:
        return "needs-opus", "touches src/core/tinglongwu/__init__.py"

    test = [c for c in checks if c.get("name") == "test"]
    if not test or any(c.get("status") != "completed" for c in test):
        return "wait", "test not finished"
    if any(c.get("conclusion") != "success" for c in test):
        return "hold", "test is red"
    # Section 17 (#1687): only these extra checks gate a dev PR; every other check run never blocks and is not waited for.
    extra = []
    if any(f["filename"].startswith("mobile/") or (f.get("previous_filename") or "").startswith("mobile/")
           for f in files):  # #1618: a phone PR also needs the mobile-apk workflow (flutter analyze + test)
        extra.append(MOBILE_CHECK)
    if any(EVAL_PATH_RE.match(f["filename"]) or EVAL_PATH_RE.match(f.get("previous_filename") or "")
           for f in files) and not (bump and bump(files)[0]):  # #1687: ASR / MT / eval PR also needs the
        # CPU mini-eval; a bump-check patch bump (only `__version__`) still merges on `test` alone (section 14)
        extra.append(EVAL_CHECK)
    for name in extra:
        got = [c for c in checks if c.get("name") == name]
        if not got or any(c.get("status") != "completed" for c in got):
            return "wait", f"{name} not finished"
        if any(c.get("conclusion") != "success" for c in got):
            return "hold", f"{name} is red"
    state = pr.get("mergeable_state")
    if state == "dirty":
        return "needs-ai", "merge conflict: resolve in place (section 7)"
    if state not in ("clean", "unstable", "has_hooks"):
        return "wait", f"mergeable_state is {state}"
    if isinstance(unresolved_threads, int):
        if unresolved_threads:
            return "needs-ai", f"{unresolved_threads} unresolved review thread(s)"
        return "merge", "all rule gates pass"
    blocking = [t for t in unresolved_threads if not minor_bot_thread(t)]
    if blocking:
        return "needs-ai", f"{len(blocking)} unresolved review thread(s) (human, Critical / Major or unrated)"
    if unresolved_threads:
        return "merge", f"all rule gates pass; {len(unresolved_threads)} Minor review-bot thread(s) to resolve and log"
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


def _login(author: dict | None) -> str:
    """GraphQL gives a bot's login without `[bot]` (`coderabbitai`, __typename Bot); REST adds it. Normalize."""
    a = author or {}
    login = a.get("login") or ""
    return f"{login}[bot]" if a.get("__typename") == "Bot" and not login.endswith("[bot]") else login


def thread_info(t: dict) -> dict:
    """One GraphQL review thread -> {id, path, author, body, url, comment_id, human_reply} (#1885)."""
    comments = (t.get("comments") or {}).get("nodes") or [{}]
    first = comments[0] or {}
    human_reply = len(comments) >= 50 or any(not _login(c.get("author")).endswith("[bot]") for c in comments[1:])
    return {"id": t["id"], "path": t.get("path"), "author": _login(first.get("author")), "body": first.get("body") or "",
            "url": first.get("url"), "comment_id": first.get("databaseId"), "human_reply": human_reply}


def _unresolved(num: int) -> list[dict]:
    """Unresolved, not outdated review threads, each with its first comment (#1885: severity and author)."""
    owner, name = REPO.split("/")
    q = ("query($o:String!,$n:String!,$p:Int!,$c:String){repository(owner:$o,name:$n){pullRequest(number:$p)"
         "{reviewThreads(first:100,after:$c){nodes{id isResolved isOutdated path "
         "comments(first:50){nodes{databaseId body url author{login __typename}}}} pageInfo{hasNextPage endCursor}}}}}")
    out, cursor = [], None
    while True:
        args = ["-f", f"query={q}", "-f", f"o={owner}", "-f", f"n={name}", "-F", f"p={num}"]
        if cursor:
            args += ["-f", f"c={cursor}"]
        conn = _gh("graphql", *args)["data"]["repository"]["pullRequest"]["reviewThreads"]
        for t in conn["nodes"]:
            if t["isResolved"] or t["isOutdated"]:
                continue
            out.append(thread_info(t))
        if not conn["pageInfo"]["hasNextPage"]:
            return out
        cursor = conn["pageInfo"]["endCursor"]


def inline_notes(num: int) -> list[dict]:
    """Inline review comments (thread replies included, resolved or not) as notes for the section 3 hold check."""
    return [{"author": c["user"]["login"], "body": c.get("body"), "at": c["created_at"]}
            for c in _gh_list(f"repos/{REPO}/pulls/{num}/comments?per_page=100")]


def gather(num: int, rn) -> tuple:
    pr = _gh(f"repos/{REPO}/pulls/{num}")
    sha = pr["head"]["sha"]
    files = _gh_list(f"repos/{REPO}/pulls/{num}/files?per_page=100")
    checks = [c for p in (_gh(f"repos/{REPO}/commits/{sha}/check-runs?per_page=100", "--paginate", "--slurp") or [])
              for c in p.get("check_runs", [])]
    runs = (_gh(f"repos/{REPO}/actions/runs?head_sha={sha}&per_page=100") or {}).get("workflow_runs", [])
    mob = [r for r in runs if r.get("name") == MOBILE_CHECK]  # newest first
    if mob:
        checks.append({"name": MOBILE_CHECK, "status": mob[0]["status"], "conclusion": mob[0]["conclusion"]})
    head_at = _ts(_gh(f"repos/{REPO}/commits/{sha}")["commit"]["committer"]["date"])
    notes = [{"author": c["user"]["login"], "body": c.get("body"), "at": c["created_at"]}
             for c in _gh_list(f"repos/{REPO}/issues/{num}/comments?per_page=100")]
    notes += [{"author": r["user"]["login"], "body": r.get("body"), "at": r["submitted_at"], "commit_id": r.get("commit_id")}
              for r in _gh_list(f"repos/{REPO}/pulls/{num}/reviews?per_page=100") if r.get("submitted_at")]
    notes += inline_notes(num)  # #1885: a "blocking" reply inside a review thread is a hard stop too
    n = linked_issue(pr.get("body"))
    m = HEAD_RE.match(pr["head"]["ref"])
    n = n or (int(m.group(1) or m.group(2)) if m else None)
    issue = None
    if n:
        try:
            issue = _gh(f"repos/{REPO}/issues/{n}")
        except subprocess.CalledProcessError as e:
            if "404" not in (e.stderr or "") and "Not Found" not in (e.stderr or ""):
                raise  # a transient error is a collection error, not "no issue"
            issue = None
    again = _gh(f"repos/{REPO}/pulls/{num}")
    if (again["head"]["sha"], again["base"]["ref"], again["state"], again.get("body")) != \
            (sha, pr["base"]["ref"], pr["state"], pr.get("body")):
        raise RuntimeError("head, base, state or body moved while reading")
    wn = None
    if any(f["filename"] == rn.WHATS_NEW_FILE for f in files):
        wn = subprocess.run(["gh", "api", f"repos/{REPO}/contents/{rn.WHATS_NEW_FILE}?ref={sha}", "-H",
                             "Accept: application/vnd.github.raw"], check=True, capture_output=True, text=True).stdout
    return (pr, files, checks, issue, notes, _unresolved(num), head_at, (lambda fs: rn.bump_exempt(fs, wn)),
            (lambda s: pass_carries(s, sha)))


COMPARE_FILE_CAP = 300  # GET compare lists at most 300 files; a longer diff cannot be checked (fail closed)


def pass_carries(pass_sha: str, head_sha: str) -> bool:
    """#1580: True when `pass_sha` is an ancestor of `head_sha` and no protected / g4 path changed in between."""
    if not re.fullmatch(r"[0-9a-f]{40}", pass_sha or "") or not head_sha:
        return False
    try:
        cmp = _gh(f"repos/{REPO}/compare/{pass_sha}...{head_sha}")
    except subprocess.CalledProcessError:
        return False  # unknown SHA or API error: no carry (fail closed)
    if (cmp.get("base_commit") or {}).get("sha") != pass_sha:
        return False  # the compare base must be exactly the approved commit
    if cmp.get("status") not in ("ahead", "identical") or cmp.get("behind_by", 1) != 0:
        return False
    files = cmp.get("files") or []
    if len(files) >= COMPARE_FILE_CAP:
        return False
    return not any(is_protected(f.get("filename", "")) or is_protected(f.get("previous_filename") or "")
                   for f in files)


def legacy_freeze() -> bool:
    pulls = _gh_list(f"repos/{REPO}/pulls?state=open&base=main&per_page=100")
    return any(p["head"]["ref"] == "dev" and (p["head"].get("repo") or {}).get("full_name") == REPO
               and p["title"].startswith("release:") for p in pulls)


def _finding_line(t: dict) -> str:
    """One line for the rolling log: severity, file and the finding's bold title (or first text line)."""
    body = BOT_BLOCK_RE.sub("", t.get("body") or "")
    m = SEVERITY_RE.search(body)
    sev = m.group(1) if m else "?"
    titles = re.findall(r"^\*\*([^*<][^*]{2,160})\*\*\s*$", body, re.M)
    text = titles[0] if titles else next((ln.strip() for ln in body.splitlines()
                                          if ln.strip() and not ln.lstrip().startswith(("<", "**<"))), "")
    return f"- {sev} `{t.get('path') or '?'}`: {text[:160]} ({t.get('url') or 'no link'})"


def minor_log_issue() -> int:
    """#1885: the open rolling `[review] minor findings` issue; created on first use."""
    found = _gh("search/issues", "-X", "GET", "-f",
                f'q=repo:{REPO} is:issue is:open in:title "{MINOR_LOG_TITLE}"', "-f", "per_page=20")
    for it in (found or {}).get("items", []):
        if it.get("title") == MINOR_LOG_TITLE and not it.get("pull_request"):
            return it["number"]
    body = ("Why: rules section 3 \"One review round\": Minor review-bot findings do not block a merge (#1885).\n"
            "Goal: one place where merge-gate logs every Minor / Trivial / Nitpick review-bot finding it resolved.\n"
            "Acceptance: each merged PR with such findings has one comment here listing them with links.\n"
            "Verify: read the comments; open a [bug] issue for any finding that is a real user-visible bug.")
    made = _gh(f"repos/{REPO}/issues", "-X", "POST", "-f", f"title={MINOR_LOG_TITLE}", "-f", f"body={body}",
               "-f", "labels[]=area:ci", "-f", "labels[]=P3", "-f", "labels[]=mode:shared", "-f", "labels[]=infra",
               "-f", "labels[]=status:proposal")
    return made["number"]


def resolve_minor(num: int, threads: list[dict]) -> None:
    """#1885: log the Minor review-bot threads, reply one line to each and resolve it (before the merge)."""
    minor = [t for t in threads if minor_bot_thread(t)]
    if not minor:
        return
    log = minor_log_issue()
    lines = "\n".join(_finding_line(t) for t in minor)
    _gh(f"repos/{REPO}/issues/{log}/comments", "-X", "POST", "-f",
        f"body=#{num}: {len(minor)} Minor review-bot finding(s), resolved by merge-gate, not fixed in the PR.\n{lines}")
    for t in minor:
        if t.get("comment_id"):
            _gh(f"repos/{REPO}/pulls/{num}/comments/{t['comment_id']}/replies", "-X", "POST", "-f",
                f"body=merge-gate: Minor finding, not fixed in this PR (rules section 3 one review round); logged on #{log}.")
        _gh("graphql", "-f", "query=mutation($t:ID!){resolveReviewThread(input:{threadId:$t}){thread{isResolved}}}",
            "-f", f"t={t['id']}")


def apply(num: int, pr: dict, action: str, reason: str, notes: list[dict] = (), threads=()) -> None:
    labels = {lb["name"] for lb in pr.get("labels", [])}
    if action == "merge":
        now = _gh(f"repos/{REPO}/pulls/{num}")  # last look right before the write: the merge API cannot bind the base
        if (now["base"]["ref"], now["head"]["sha"], now.get("body")) != ("dev", pr["head"]["sha"], pr.get("body")):
            raise RuntimeError("base, head or body changed before merge; not merged")
        if "release-fix" not in (pr.get("body") or "") and legacy_freeze():
            raise RuntimeError("legacy release freeze started; not merged")
        if not isinstance(threads, int):
            resolve_minor(num, list(threads))
        subprocess.run(["gh", "api", "-X", "PUT", f"repos/{REPO}/pulls/{num}/merge", "-f", "merge_method=squash",
                        "-f", f"sha={pr['head']['sha']}"], check=True)
    elif action in ("needs-opus", "needs-ai"):
        label = NEEDS_OPUS if action == "needs-opus" else NEEDS_AI
        marker = f"merge-gate: {label}:"
        if label not in labels:
            subprocess.run(["gh", "api", "-X", "POST", f"repos/{REPO}/issues/{num}/labels", "-f", f"labels[]={label}"],
                           check=True, capture_output=True)
        if not any((n.get("body") or "").startswith(marker) for n in notes):  # retried on its own if it failed
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
            pr, files, checks, issue, notes, threads, head_at, bump, carry = gather(num, rn)
            action, reason = evaluate(pr, files, checks, issue, notes, threads, head_at, bump, freeze, carry)
            print(f"#{num} {action}: {reason}")
            if a.apply:
                apply(num, pr, action, reason, notes, threads)
        except Exception as e:  # one bad PR never stops the rest
            print(f"#{num} error: {e}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
