"""#944: fail a PR whose commits carry an unknown git identity.

A placeholder identity such as `x <x@x>` gets linked by GitHub to whichever stranger owns that email, and a squash
merge copies it into `Co-authored-by:`, so the stranger shows up as a repo contributor. This check reads the PR's
commits (the JSON list from `GET repos/{repo}/pulls/{n}/commits`) and fails when any author, committer or
`Co-authored-by:` email is outside ALLOWED_EMAILS / ALLOWED_DOMAINS, the PR author's own GitHub noreply email
(so fork contributors keep working) and, #1443 / #1309, any email GitHub links to the PR author's account (a commit
whose top-level `author.login` / `committer.login` is the PR author), so a contributor commits with their own identity.

Usage: python scripts/commit_identity.py commits.json --author-login LOGIN --author-id ID [--expected-count N]
Exit 0 = every identity is known, 1 = unknown identities (each printed with its commit) or an incomplete commit list
(the commits API returns at most 250, so a PR with >= 250 commits cannot be checked and fails), 2 = unreadable input.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ALLOWED_EMAILS = frozenset({
    "243713070+sunososobro-hub@users.noreply.github.com",  # owner noreply (what the cloud routines commit as)
    "sunososobro-hub@users.noreply.github.com",  # owner noreply, legacy form without the id
    "sunososobro@gmail.com",  # owner
    "41898282+github-actions[bot]@users.noreply.github.com",  # github-actions[bot]
    "noreply@github.com",  # `GitHub` committer of web-flow merges and squash commits
})
ALLOWED_DOMAINS = ("@anthropic.com",)
API_LIMIT = 250  # GET pulls/{n}/commits lists at most 250 commits, whatever the pagination  # Claude Code sessions (noreply@anthropic.com)
CO_AUTHOR = re.compile(r"^co-authored-by:.*<([^<>]*)>\s*$", re.IGNORECASE | re.MULTILINE)


def author_noreply(login: str, user_id: str) -> set[str]:
    """The GitHub noreply emails of the PR author (`ID+login@...` and the legacy `login@...`)."""
    if not login:
        return set()
    out = {f"{login}@users.noreply.github.com".lower()}
    if user_id:
        out.add(f"{user_id}+{login}@users.noreply.github.com".lower())
    return out


def author_emails(commits: list, login: str) -> set[str]:
    """#1443: emails GitHub resolved to the PR author's account on these commits (GitHub only links verified emails)."""
    if not login:
        return set()
    out = set()
    for c in commits:
        info = c.get("commit") or {}
        for role in ("author", "committer"):
            account = c.get(role) or {}
            email = ((info.get(role) or {}).get("email") or "").strip().lower()
            if email and str(account.get("login") or "").lower() == login.lower():
                out.add(email)
    return out


def allowed(email: str, extra: set[str]) -> bool:
    e = email.strip().lower()
    return e in ALLOWED_EMAILS or e in extra or (e.endswith(ALLOWED_DOMAINS) and e.count("@") == 1)


def problems(commits: list, login: str = "", user_id: str = "") -> list[str]:
    """One line per unknown identity: `<short sha> <role> <name> <email>`."""
    extra = author_noreply(login, user_id) | author_emails(commits, login)
    out = []
    for c in commits:
        sha = str(c.get("sha", "?"))[:7]
        info = c.get("commit") or {}
        for role in ("author", "committer"):
            who = info.get(role) or {}
            email = who.get("email") or ""
            if not allowed(email, extra):
                out.append(f"{sha} {role} {who.get('name', '?')} <{email}>")
        for email in CO_AUTHOR.findall(info.get("message") or ""):
            if not allowed(email, extra):
                out.append(f"{sha} Co-authored-by <{email}>")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("commits", type=Path, help="JSON list from GET repos/{repo}/pulls/{n}/commits")
    ap.add_argument("--author-login", default="", help="PR author's login (their noreply email is allowed)")
    ap.add_argument("--author-id", default="", help="PR author's numeric user id")
    ap.add_argument("--expected-count", type=int, default=None,
                    help="the PR's commit count (pulls/{n} .commits); a shorter list or >= 250 commits fails")
    a = ap.parse_args(argv)
    try:
        data = json.loads(a.commits.read_text("utf-8"))
    except (OSError, ValueError) as e:
        print(f"commit identity: cannot read {a.commits}: {e}")
        return 2
    # `gh api --paginate` concatenates pages as separate arrays only with --slurp; accept both shapes
    commits = [c for page in data for c in page] if data and all(isinstance(p, list) for p in data) else data
    if not isinstance(commits, list):
        print("commit identity: expected a JSON list of commits")
        return 2
    if a.expected_count is not None and (a.expected_count >= API_LIMIT or len(commits) != a.expected_count):
        print(f"FAIL: commit identity: the PR has {a.expected_count} commit(s) but {len(commits)} were listed "
              f"(the API lists at most {API_LIMIT}); split or squash the PR so every commit can be checked.")
        return 1
    bad = problems(commits, a.author_login, a.author_id)
    for line in bad:
        print(f"FAIL: unknown git identity: {line}")
    if bad:
        print("Set `git config user.name sunososobro-hub` and `git config user.email "
              "243713070+sunososobro-hub@users.noreply.github.com` (rules section 2), then rewrite these commits.")
        return 1
    print(f"commit identity: {len(commits)} commit(s), all identities known")
    return 0


if __name__ == "__main__":
    sys.exit(main())
