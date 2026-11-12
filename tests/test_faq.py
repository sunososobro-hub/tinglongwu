"""#349: positioning FAQ entry stays short, has a small table, and is linked from README."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAQ = ROOT / "docs" / "faq.md"
ENTRY = "How is this different from Chrome's built-in captions or browser extensions?"


def _entry() -> str:
    body = FAQ.read_text("utf-8").split("## " + ENTRY, 1)[1]
    return re.split(r"^## ", body, flags=re.M)[0]


def test_entry_at_most_150_words():
    text = re.sub(r"\|[-| ]+\|", " ", _entry())
    assert len(re.findall(r"[^\s|]+", text)) <= 150


def test_table_has_at_most_6_data_rows():
    rows = [l for l in _entry().splitlines() if l.startswith("|")]
    assert 2 <= len(rows) - 2 <= 6  # minus header and separator


def test_readme_links_faq_in_one_line():
    lines = [l for l in (ROOT / "README.md").read_text("utf-8").splitlines() if "docs/faq.md" in l]
    assert len(lines) == 1
