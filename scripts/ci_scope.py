"""Decide how much of the `test` job a PR needs (#1221).

Owner 2026-11-16: "總不會我改 readme 也流水線吧". A PR that only touches docs or the stats site
should not wait for the full suite, but the `test` check must still report (it is the required check).

Scopes:
- docs: every changed file is documentation or a static asset. Only the doc-sweeping tests (DOCS_TESTS) and the
  tests that mention a changed file run; no apt, no GUI, no offline suite.
- site: every changed file is docs or the stats/roadmap/docs-page generator; at least one is the latter.
  The site tests run, plus the tests that mention a changed docs file.
- full: anything else, an empty list, or a list the API may have cut short. The job runs as before.

Usage: python scripts/ci_scope.py CHANGED_FILES.txt [--tests-dir tests]
Prints `scope=<docs|site|full>` and `tests=<space separated test files>` (GITHUB_OUTPUT lines).
"""
from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path

# the pulls/N/files API returns at most 3000 files; a list that long may be incomplete
MAX_LISTED_FILES = 3000

# never docs, even inside docs/: read by code or by the agents' rules
NOT_DOCS = ("docs/support.json", ".agent/*", ".github/*")
# the gate itself: a PR that edits the classifier or the job that runs it always gets the full suite,
# so it cannot label itself docs (Opus review on #1223). ci.yml also runs the BASE branch copy of this file.
SELF = ("scripts/ci_scope.py", ".github/workflows/ci.yml")
DOCS = ("*.md", "docs/*", "assets/*", "LICENSE", "LICENSE.*")
SITE = (
    "scripts/stats_dashboard*",
    "scripts/render_roadmap.py",
    "scripts/build_docs_page.py",
    "docs/roadmap.html",
    "tests/test_stats*.py",
    "tests/test_render_roadmap*.py",
    "tests/test_*dashboard*.py",
    "tests/test_build_docs_page*.py",
)
# tests that sweep every doc (link checks, README size, docs/*.md globs): they never name the file they read
DOCS_TESTS = ("test_docs*.py", "test_readme*.py", "test_*links*.py")
SITE_TESTS = ("test_stats*.py", "test_render_roadmap*.py", "test_*dashboard*.py", "test_build_docs_page*.py")


def _match(path: str, patterns) -> bool:
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def kind(path: str) -> str:
    """'site', 'docs' or 'full' for one changed path (site is checked first: docs/roadmap.html is site)."""
    if _match(path, SELF) or _match(path, NOT_DOCS):
        return "full"
    if _match(path, SITE):
        return "site"
    if _match(path, DOCS):
        return "docs"
    return "full"


def classify(files) -> str:
    files = [f for f in files if f]
    if not files or len(files) >= MAX_LISTED_FILES:
        return "full"
    kinds = {kind(f) for f in files}
    if "full" in kinds:
        return "full"
    return "site" if "site" in kinds else "docs"


def tests_mentioning(files, tests_dir: Path) -> list[str]:
    """Test files whose source names a changed file (full path or file name). Over-matching only costs time."""
    needles = set()
    for f in files:
        if f:
            needles.add(f)
            needles.add(Path(f).name)
    hits = []
    for t in sorted(tests_dir.glob("test_*.py")):
        text = t.read_text(encoding="utf-8", errors="replace")
        if any(n in text for n in needles):
            hits.append(t.as_posix())
    return hits


def select_tests(scope: str, files, tests_dir: Path) -> list[str]:
    if scope == "full":
        return []
    picked = set(tests_mentioning(files, tests_dir))
    for pat in DOCS_TESTS:
        picked.update(t.as_posix() for t in tests_dir.glob(pat))
    if scope == "site":
        for pat in SITE_TESTS:
            picked.update(t.as_posix() for t in tests_dir.glob(pat))
    return sorted(picked)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", help="text file with one changed path per line")
    ap.add_argument("--tests-dir", default="tests")
    a = ap.parse_args(argv)
    files = Path(a.files).read_text(encoding="utf-8").splitlines()
    scope = classify(files)
    tests = select_tests(scope, files, Path(a.tests_dir))
    print(f"scope={scope}")
    print("tests=" + " ".join(tests))
    return 0


if __name__ == "__main__":
    sys.exit(main())
