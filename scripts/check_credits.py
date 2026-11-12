#!/usr/bin/env python3
"""Release gate: every non-owner author of a PR merged since the last tag must be credited.

Credited = the login appears (case-insensitive, as a whole word) in CREDITS.md or in the release notes file.

Usage:   python scripts/check_credits.py [--repo OWNER/REPO] [--owner LOGIN] [--credits CREDITS.md]
                                         [--notes FILE] [--since ISO_DATE] [--prs-json FILE]
Exit:    0 all credited, 1 someone missing (logins printed), 2 could not query GitHub.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_REPO = "sunososobro-hub/tinglongwu"
DEFAULT_OWNER = "sunososobro-hub"
BOT_SUFFIX = "[bot]"


def external_authors(prs, since, owner):
    """Logins of non-owner, non-bot authors of PRs merged at/after `since` (ISO string; '' = all)."""
    out = set()
    for pr in prs:
        merged = pr.get("merged_at")
        login = (pr.get("user") or {}).get("login", "")
        if not merged or (since and merged < since):
            continue
        if not login or login.lower() == owner.lower() or login.endswith(BOT_SUFFIX):
            continue
        out.add(login)
    return sorted(out, key=str.lower)


def missing_credits(authors, *texts):
    blob = "\n".join(texts)
    return [a for a in authors
            if not re.search(r"(?<![\w-])@?" + re.escape(a) + r"(?![\w-])", blob, re.IGNORECASE)]


def _gh(path):
    r = subprocess.run(["gh", "api", "--paginate", "--slurp", path], capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "gh api failed")
    data = json.loads(r.stdout)
    return [x for page in data for x in page] if data and isinstance(data[0], list) else data


def last_tag_date(repo):
    tags = _gh(f"repos/{repo}/tags?per_page=1")
    if not tags:
        return ""
    commit = _gh(f"repos/{repo}/commits/{tags[0]['commit']['sha']}")
    return commit["commit"]["committer"]["date"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--owner", default=DEFAULT_OWNER)
    ap.add_argument("--credits", default="CREDITS.md")
    ap.add_argument("--notes", help="release notes file (optional)")
    ap.add_argument("--since", help="ISO date; default = date of the latest tag")
    ap.add_argument("--prs-json", help="offline: JSON list of PRs (user.login, merged_at) instead of querying GitHub")
    a = ap.parse_args(argv)
    try:
        if a.prs_json:
            prs, since = json.loads(Path(a.prs_json).read_text("utf-8")), a.since or ""
        else:
            since = a.since if a.since is not None else last_tag_date(a.repo)
            prs = _gh(f"repos/{a.repo}/pulls?state=closed&per_page=100")
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as e:
        print(f"check_credits: cannot query GitHub: {e}", file=sys.stderr)
        return 2
    texts = [p.read_text("utf-8") for p in (Path(a.credits), Path(a.notes) if a.notes else None)
             if p and p.is_file()]
    authors = external_authors(prs, since, a.owner)
    missing = missing_credits(authors, *texts)
    if missing:
        print("Missing from CREDITS.md / release notes: " + ", ".join(missing))
        return 1
    print(f"OK: {len(authors)} external author(s) credited")
    return 0


if __name__ == "__main__":
    sys.exit(main())
