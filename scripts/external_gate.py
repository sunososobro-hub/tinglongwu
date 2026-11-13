#!/usr/bin/env python3
"""Deterministic gate for outside (fork / non-owner) pull requests (#352 part 1).

Decides the lane for an external PR:
  auto-merge  every rule below holds; the PR may be merged automatically with an authorship-preserving
              method (MERGE_METHODS: rebase, falling back to a merge commit; never squash).
  owner       at least one rule failed; the owner must be notified with the failed rules + the review verdict.

Rules (script first; the LLM review can only block, never override a failed rule):
  r0_snapshot  (--pr) head and base SHA are the same before and after reading files + check runs
  r1_size      total changed lines (additions + deletions) <= MAX_CHANGED_LINES
  r2_paths     only `tinglongwu/**/*.py` and `tests/**/*.py`; none of PROTECTED_FILES; no deleted files;
               with --pr, the file list must be complete (== changed_files), else fail closed
  r3_denylist  added lines contain no network / process / dynamic-code primitive (DENYLIST); every
               changed file has a readable patch (a binary or truncated diff cannot be inspected)
               (a text denylist cannot see every disguise, e.g. names built from strings at run time;
               it is a cheap first filter and r5 plus the owner lane for anything protected stay the backstop)
  r4_tests     at least one test file under tests/ added or modified, and the check runs are all green
  r5_review    the AI review verdict is exactly "PASS" (PR title/body/comments are untrusted data and
               are never read by this script)

Usage:
  python scripts/external_gate.py --files files.json [--checks checks.json] [--review PASS]
  python scripts/external_gate.py --pr 108 [--review PASS]      # reads files + check runs with `gh api`
files.json is the JSON of `GET repos/{R}/pulls/{N}/files`; checks.json is the JSON of
`GET repos/{R}/commits/{sha}/check-runs` (or just its "check_runs" list).
Prints one JSON object {"lane", "failed", "reasons", "merge_methods"}. Exit 0 = auto-merge, 1 = owner lane.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from fnmatch import fnmatchcase

REPO = "sunososobro-hub/tinglongwu"
MAX_CHANGED_LINES = 50  # number from #352 rule 1
MERGE_METHODS = ("rebase", "merge")  # keeps the contributor as commit author; squash is never used
ALLOWED_GLOBS = ("tinglongwu/*.py", "tests/*.py")  # fnmatch "*" also crosses "/", so subpackages match
# Inside the allowed folders but still owner-only: network guard / offline mode, model download + pins,
# install hints, and the test harness itself (a conftest change can skip or neutralise any test).
PROTECTED_FILES = frozenset({
    "tinglongwu/offline.py",
    "tinglongwu/netreport.py",
    "tinglongwu/models.py",
    "tinglongwu/llamacheck.py",
    "tinglongwu/ollama.py",  # the one network client (loopback-only guard in _endpoint)
    "tests/test_no_network_code.py",  # its ALLOWED list whitelists network imports
    "tests/conftest.py",
})
DENYLIST = ("socket", "urllib", "requests", "http", "subprocess", "os.system", "eval(", "exec(",
            "__import__", "importlib", "pickle", "base64", "ctypes")  # list from #352 rule 3
_DENY_RX = re.compile("|".join([re.escape(w) for w in DENYLIST] + [r"\b(?:eval|exec|compile)\s*\(", r"\bos\s*\.\s*system\b",
                       r"\bsystem\b", r"popen", r"spawn", r"\bfork\b", r"\bexec[lv]p?e?\b",
                       r"\bgetattr\s*\(", r"\bglobals\s*\(", r"\bvars\s*\(", r"__builtins__"]),
                      re.IGNORECASE)  # extra patterns catch "eval (x)" and "os . system" (whitespace is legal Python)
GREEN_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})


def _names(f: dict) -> list[str]:
    names = [f.get("filename", "")]
    if f.get("previous_filename"):
        names.append(f["previous_filename"])  # a rename must not move a protected file out of the way
    return names


def path_problems(files: list[dict]) -> list[str]:
    out = []
    for f in files:
        if f.get("status") == "removed":
            out.append(f"{f.get('filename')}: file deleted")
            continue
        for name in _names(f):
            if name in PROTECTED_FILES or name.rsplit("/", 1)[-1] == "conftest.py":  # any conftest can skip all tests
                out.append(f"{name}: protected file")
            elif not any(fnmatchcase(name, g) for g in ALLOWED_GLOBS):
                out.append(f"{name}: outside tinglongwu/**/*.py and tests/**/*.py")
    return out


def added_lines(patch: str) -> list[str]:
    """'+' lines inside hunks. A '+++' file header only counts before the first '@@'; inside a hunk,
    '+++x' is the added source line '++x' (repeated unary plus is valid Python)."""
    out, in_hunk = [], False
    for ln in patch.splitlines():
        if ln.startswith("@@"):
            in_hunk = True
        elif ln.startswith("+") and (in_hunk or not ln.startswith("+++")):
            out.append(ln[1:])
    return out


def denylist_hits(files: list[dict]) -> list[str]:
    out = []
    for f in files:
        name = f.get("filename", "")
        patch = f.get("patch")
        if patch is None:
            if f.get("changes", 0) or f.get("status") == "added":
                out.append(f"{name}: no readable patch (binary or too large)")
            continue
        for ln in added_lines(patch):
            m = _DENY_RX.search(unicodedata.normalize("NFKC", ln))  # Python NFKC-folds identifiers (PEP 3131)
            if m:
                out.append(f"{name}: adds '{m.group(0)}'")
    return out


def checks_green(check_runs: list[dict] | None) -> tuple[bool, str]:
    if not check_runs:
        return False, "no check runs"
    bad = [c.get("name", "?") for c in check_runs
           if c.get("status") != "completed" or c.get("conclusion") not in GREEN_CONCLUSIONS]
    if bad:
        return False, "not green: " + ", ".join(sorted(bad))
    return True, ""


def evaluate(files: list[dict], check_runs: list[dict] | None, review: str | None, pr: dict | None = None) -> dict:
    """pr: the GET pulls/N object, when known. Its totals win over the file list, and a file list shorter than
    pr["changed_files"] (API truncation) fails closed: unseen files cannot be checked."""
    reasons: dict[str, str] = {}
    total = sum(int(f.get("additions", 0)) + int(f.get("deletions", 0)) for f in files)
    if pr is not None:
        total = max(total, int(pr.get("additions", 0)) + int(pr.get("deletions", 0)))
    if total > MAX_CHANGED_LINES:
        reasons["r1_size"] = f"{total} changed lines > {MAX_CHANGED_LINES}"
    if pr is not None and pr.get("head_moved"):
        reasons["r0_snapshot"] = "PR head/base changed while files and checks were read; re-run the gate"
    if pr is not None and len(files) != int(pr.get("changed_files", -1)):
        reasons["r2_paths"] = f"file list has {len(files)} files, PR reports {pr.get('changed_files')}"
    elif not files:
        reasons["r2_paths"] = "no changed files"
    elif p := path_problems(files):
        reasons["r2_paths"] = "; ".join(p)
    if d := denylist_hits(files):
        reasons["r3_denylist"] = "; ".join(d)
    tests_touched = any(f.get("filename", "").startswith("tests/") and f.get("status") in ("added", "modified")
                        for f in files)
    ok, why = checks_green(check_runs)
    t = ([] if tests_touched else ["no test added or changed"]) + ([] if ok else [why])
    if t:
        reasons["r4_tests"] = "; ".join(t)
    if (review or "").strip() != "PASS":
        reasons["r5_review"] = f"review verdict is {review!r}, not 'PASS'"
    return {
        "lane": "owner" if reasons else "auto-merge",
        "failed": sorted(reasons),
        "reasons": reasons,
        "merge_methods": list(MERGE_METHODS),
    }


def _gh(path: str, paginate: bool = False):
    cmd = ["gh", "api", path] + (["--paginate", "--slurp"] if paginate else [])
    out = subprocess.run(cmd, check=True, stdout=subprocess.PIPE).stdout
    return json.loads(out)


def fetch_pr(n: int, gh=_gh) -> tuple[list[dict], list[dict], dict]:
    """Every page of files and check runs (--slurp returns a list of pages)."""
    pr = gh(f"repos/{REPO}/pulls/{n}")
    files = [f for page in gh(f"repos/{REPO}/pulls/{n}/files?per_page=100", paginate=True) for f in page]
    pages = gh(f"repos/{REPO}/commits/{pr['head']['sha']}/check-runs?per_page=100", paginate=True)
    runs = [r for page in pages for r in page.get("check_runs", [])]
    after = gh(f"repos/{REPO}/pulls/{n}")  # files are read by PR number, checks by SHA: same snapshot only if
    if (after["head"]["sha"], after.get("base", {}).get("sha")) != (pr["head"]["sha"], pr.get("base", {}).get("sha")):
        pr = {**pr, "head_moved": True}  # the head/base did not move in between; else fail closed
    return files, runs, pr


def _load(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--files", help="JSON of GET pulls/N/files")
    src.add_argument("--pr", type=int, help="read files + check runs of this PR with `gh api`")
    ap.add_argument("--checks", help="JSON of GET commits/SHA/check-runs (with --files)")
    ap.add_argument("--review", default=None, help="AI review verdict; only PASS can allow auto-merge")
    a = ap.parse_args(argv)
    pr = None
    if a.pr is not None:
        files, runs, pr = fetch_pr(a.pr)
    else:
        files = _load(a.files)
        runs = None
        if a.checks:
            c = _load(a.checks)
            runs = c.get("check_runs", []) if isinstance(c, dict) else c
    res = evaluate(files, runs, a.review, pr)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res["lane"] == "auto-merge" else 1


if __name__ == "__main__":
    sys.exit(main())
