"""#345 part 1: docs/support.md holds the release policy and a platform support table; the README links it once.

The policy text follows the owner decision in #478 (plain 0.x tags are normal releases, the newest is "Latest";
only suffixed tags are pre-releases). The last test runs the release workflow's rule so the doc and the
workflow cannot drift apart."""
import re
from pathlib import Path

from tests.test_release_prerelease_rule import _flags

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = ROOT / "docs" / "support.md"
PLATFORMS = ("Windows 11", "Ubuntu 24.04", "macOS")
COLUMNS = ["Platform", "Install method", "Smoke status", "Known issues"]


def _table(md: str) -> list[list[str]]:
    rows = []
    for line in md.splitlines():
        if line.startswith("|") and not re.fullmatch(r"\|[-| ]+\|", line):
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    return rows


def _links(md: str) -> list[str]:
    return re.findall(r"\]\(([^)\s]+)\)", md)


def test_support_table_has_one_row_per_platform_with_all_columns():
    rows = _table(SUPPORT.read_text("utf-8"))
    assert rows[0] == COLUMNS
    body = rows[1:]
    assert len(body) == len(PLATFORMS)
    for p in PLATFORMS:
        hits = [r for r in body if r[0] == p]
        assert len(hits) == 1, p
        assert len(hits[0]) == len(COLUMNS) and all(hits[0]), p


def test_support_doc_links_the_stable_bar_issues():
    links = _links(SUPPORT.read_text("utf-8"))
    for n in (294, 328, 332, 341):
        assert f"https://github.com/sunososobro-hub/tinglongwu/issues/{n}" in links, n


def test_readme_links_support_doc_exactly_once():
    assert _links((ROOT / "README.md").read_text("utf-8")).count("docs/support.md") == 1


def test_support_doc_policy_matches_the_release_workflow():
    s = SUPPORT.read_text("utf-8")
    assert "0.x" in s and "normal GitHub release" in s and "pre-release" in s
    # what the doc promises: a plain 0.x tag is not a pre-release (stays Latest), a suffixed tag is
    assert _flags("v0.4.0") == ""
    assert _flags("v0.4.0-beta.1") == "--prerelease"
