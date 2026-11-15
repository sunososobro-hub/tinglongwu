#!/usr/bin/env python3
"""#953: make the release freeze (rules section 14) a mechanical check.

    python scripts/release_freeze.py check --pr <N> [--repo OWNER/REPO]

Exits 1 with `release freeze vX.Y.Z — merge only release-fix` when dev is frozen and the PR body lacks
`release-fix`; exits 0 otherwise. Frozen = an open PR titled `release: vX.Y.Z` for dev's `__version__` exists
AND the tag `vX.Y.Z` does not exist yet. Network only through the `gh` CLI (GH_TOKEN).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys

VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.M)
RELEASE_TITLE_RE = re.compile(r"^release:\s*v(\d+\.\d+\.\d+)\s*$")
RELEASE_FIX_RE = re.compile(r"release-fix", re.I)


def is_frozen(dev_version: str, open_release_pr_titles, tags) -> bool:
    """True when an open `release: v<dev_version>` PR exists and tag `v<dev_version>` does not."""
    if f"v{dev_version}" in set(tags):
        return False
    for title in open_release_pr_titles:
        m = RELEASE_TITLE_RE.match((title or "").strip())
        if m and m.group(1) == dev_version:
            return True
    return False


def blocks_merge(frozen: bool, pr_body: str | None) -> bool:
    """A frozen dev only takes PRs whose body says `release-fix`."""
    return frozen and not RELEASE_FIX_RE.search(pr_body or "")


def _gh(path: str):
    out = subprocess.run(["gh", "api", "--paginate", path], capture_output=True, text=True, check=True).stdout
    return json.loads(out) if out.strip() else []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--pr", type=int, required=True)
    c.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "sunososobro-hub/tinglongwu"))
    a = ap.parse_args(argv)
    r = a.repo
    init = _gh(f"repos/{r}/contents/tinglongwu/__init__.py?ref=dev")
    text = base64.b64decode(init["content"]).decode() if isinstance(init, dict) else ""
    m = VERSION_RE.search(text)
    if not m:
        print("freeze-guard: cannot read dev __version__; not blocking")
        return 0
    version = m.group(1)
    prs = _gh(f"repos/{r}/pulls?state=open&base=main&per_page=100")
    titles = [p.get("title", "") for p in prs]
    tags = [t["name"] for t in _gh(f"repos/{r}/tags?per_page=100")]
    pr = _gh(f"repos/{r}/pulls/{a.pr}")
    if blocks_merge(is_frozen(version, titles, tags), pr.get("body")):
        print(f"release freeze v{version} — merge only release-fix")
        return 1
    print("freeze-guard: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
