"""#678: Discussions is linked from README (3 languages), issue chooser, suggestion form, CONTRIBUTING."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
URL = "https://github.com/sunososobro-hub/tinglongwu/discussions"


def _links(md: str):
    import re
    return re.findall(r"\]\((https?://[^)\s]+)\)", md)


def test_readmes_link_discussions():
    for name in ("README.md", "README.zh-TW.md", "README.zh-CN.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert URL in _links(text), name


def test_issue_chooser_has_discussions_contact_link():
    cfg = yaml.safe_load((ROOT / ".github/ISSUE_TEMPLATE/config.yml").read_text(encoding="utf-8"))
    assert cfg["blank_issues_enabled"] is False
    assert URL in [c["url"] for c in cfg["contact_links"]]


def test_suggestion_form_points_to_discussions():
    form = yaml.safe_load((ROOT / ".github/ISSUE_TEMPLATE/suggestion.yml").read_text(encoding="utf-8"))
    md = [b for b in form["body"] if b["type"] == "markdown"]
    assert md and URL in md[0]["attributes"]["value"]
    assert [b["type"] for b in form["body"]].count("textarea") == 2  # form still intact


def test_contributing_mentions_discussions():
    assert URL in _links((ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8"))
