"""The GitHub issue forms must ask for the fields triage needs (behavioural: parse the forms, check the fields)."""
import re
from pathlib import Path

D = Path(__file__).resolve().parent.parent / ".github" / "ISSUE_TEMPLATE"


def parse_form(path):
    """Tiny parser for the restricted form layout (no PyYAML dependency): {id: {'type', 'required'}}."""
    fields, cur = {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s+- type:\s*(\w+)", line)
        if m:
            cur = {"type": m.group(1), "required": False}
            continue
        m = re.match(r"\s+id:\s*(\S+)", line)
        if m and cur is not None:
            fields[m.group(1)] = cur
        if re.match(r"\s+required:\s*true", line) and cur is not None:
            cur["required"] = True
    return fields


def test_all_three_forms_exist():
    names = {p.name for p in D.glob("*.yml")}
    assert {"bug.yml", "translation-error.yml", "suggestion.yml"} <= names


def test_bug_and_translation_require_triage_fields():
    for name in ("bug.yml", "translation-error.yml"):
        f = parse_form(D / name)
        for fid in ("version", "os", "mode", "langpair", "happened", "expected"):
            assert f[fid]["required"], (name, fid)
        assert f["log"]["type"] == "textarea" and not f["log"]["required"], name


def test_suggestion_requires_core_fields():
    f = parse_form(D / "suggestion.yml")
    assert all(f[k]["required"] for k in ("happened", "expected", "version", "os"))


def test_config_has_readme_link_and_blank_issues_off():
    text = (D / "config.yml").read_text(encoding="utf-8")
    links = re.findall(r"url:\s*(\S+)", text)
    assert any(u.endswith("#readme") for u in links)
    assert re.search(r"blank_issues_enabled:\s*false", text)
