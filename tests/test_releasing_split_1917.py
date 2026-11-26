"""#1917: docs/RELEASING.md is an index; each release step lives in docs/releasing/*.md.
The index must list every step file once, in step order, and the files must keep the doc's rules."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "docs" / "RELEASING.md"
STEP_DIR = ROOT / "docs" / "releasing"
_LINK = re.compile(r"^- \[[^\]]+\]\((releasing/[^)]+\.md)\)$", re.M)


def _listed():
    return [ROOT / "docs" / m for m in _LINK.findall(INDEX.read_text(encoding="utf-8"))]


def _steps(text):
    """Top-level numbered steps (`N. ...` at column 0)."""
    return [int(n) for n in re.findall(r"(?m)^(\d+)\. ", text)]


def test_index_lists_every_step_file_once():
    listed = _listed()
    assert listed and len(set(listed)) == len(listed)
    assert sorted(listed) == sorted(STEP_DIR.glob("*.md"))
    for p in listed:
        assert p.is_file(), p


def test_index_keeps_no_step_text():
    assert _steps(INDEX.read_text(encoding="utf-8")) == []


def test_steps_read_in_order_across_files():
    text = "\n".join(p.read_text(encoding="utf-8") for p in _listed())
    assert _steps(text) == [1, 2, 3, 4, 5, 6, 7]
    for needle in ("**Bump.**", "**Snapshot and release PR.**", "**Auto-merge.**", "**Publish.**",
                   "**Rollback**", "**Owner one-time actions**", "Release policy (owner decision in #478",
                   "If the workflow fails before the release is published"):
        assert text.count(needle) == 1, needle


def test_each_step_file_has_one_title_and_links_back():
    for p in _listed():
        s = p.read_text(encoding="utf-8")
        assert len(re.findall(r"(?m)^# ", s)) == 1, p.name
        assert "](../RELEASING.md)" in s, p.name


def test_no_flag_emoji_in_step_files():  # same rule as the READMEs (test_readme_install_urls.py)
    for p in _listed():
        assert not re.search("[\U0001F1E6-\U0001F1FF]", p.read_text(encoding="utf-8")), p.name
