import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


def test_readme_is_short():
    assert README.stat().st_size <= 6 * 1024


def test_readme_relative_links_resolve():
    text = README.read_text("utf-8")
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if target.startswith(("http", "#")):
            continue
        assert (ROOT / target.split("#")[0]).exists(), target


def test_docs_relative_links_resolve():
    for md in (ROOT / "docs").glob("*.md"):
        for target in re.findall(r"\]\(([^)\s]+)\)", md.read_text("utf-8")):
            if target.startswith(("http", "#")):
                continue
            assert (md.parent / target.split("#")[0]).exists(), (md.name, target)


def test_moved_sections_live_in_docs():
    for name in ("install", "usage", "models", "privacy", "benchmarks",
                 "troubleshooting", "linux-reverse", "licenses", "disclaimer"):
        assert (ROOT / "docs" / f"{name}.md").stat().st_size > 500, name
