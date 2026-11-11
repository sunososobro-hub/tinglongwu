"""#166: README is slimmed in steps by moving sections verbatim to docs/; no link may break on the way.

README_MAX_BYTES is a ratchet: each split PR lowers it, the last one sets the #166 goal (6 KB).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
README_MAX_BYTES = 28 * 1024  # part 1 of #166 (was ~39 KB); goal 6 * 1024
MOVED = ("licenses", "benchmarks", "linux-reverse", "disclaimer")


def _slug(heading: str) -> str:
    # GitHub heading anchor: lower case, drop punctuation except '-' and '_', spaces -> '-'
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def _anchors(md: Path) -> set[str]:
    return {_slug(m) for m in re.findall(r"^#+ (.+)$", md.read_text("utf-8"), re.M)}


def _links(md: Path):
    return re.findall(r"\]\(([^)\s]+)\)", md.read_text("utf-8"))


def _check(md: Path) -> list[str]:
    bad = []
    for target in _links(md):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, anchor = target.partition("#")
        dest = (md.parent / path).resolve() if path else md
        if not dest.exists():
            bad.append(target)
        elif anchor and dest.suffix == ".md" and anchor not in _anchors(dest):
            bad.append(target)
    return bad


def test_readme_size_budget():
    assert README.stat().st_size <= README_MAX_BYTES


def test_readme_links_and_anchors_resolve():
    assert _check(README) == []


def test_docs_links_and_anchors_resolve():
    for md in sorted((ROOT / "docs").glob("*.md")):
        assert _check(md) == [], md.name


def test_moved_sections_live_in_docs_and_readme_links_them():
    links = _links(README)
    for name in MOVED:
        doc = ROOT / "docs" / f"{name}.md"
        assert doc.stat().st_size > 500, name
        assert f"docs/{name}.md" in links, name


def test_slug_matches_github_rules():
    assert _slug("Acceptable Use / Consent") == "acceptable-use--consent"
    assert _slug("Linux reverse mode (experimental, untested)") == "linux-reverse-mode-experimental-untested"
