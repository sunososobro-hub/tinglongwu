"""#345: docs/support.md holds the release policy and a platform support table; the README links it once."""
import re
from pathlib import Path

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


def test_support_table_has_one_row_per_platform_with_all_columns():
    rows = _table(SUPPORT.read_text("utf-8"))
    assert rows[0] == COLUMNS
    body = rows[1:]
    for p in PLATFORMS:
        hits = [r for r in body if r[0].startswith(p)]
        assert len(hits) == 1, p
        assert len(hits[0]) == len(COLUMNS) and all(hits[0]), p


def test_support_doc_states_prerelease_policy_and_stable_bar():
    s = SUPPORT.read_text("utf-8")
    assert "pre-release" in s and "0.x" in s
    for n in (294, 328, 332, 341):
        assert f"issues/{n})" in s, n


def test_readme_links_support_doc_exactly_once():
    links = re.findall(r"\]\(([^)\s]+)\)", (ROOT / "README.md").read_text("utf-8"))
    assert links.count("docs/support.md") == 1
