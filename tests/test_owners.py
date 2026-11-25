"""#1918: per-folder OWNERS files read by the merge gate and triage."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import owners  # noqa: E402
import triage_issue  # noqa: E402
from merge_gate import is_protected  # noqa: E402

REQUIRED = ("src", "mobile", "scripts", ".agent", ".github", "docs", "tests")


def _tree(tmp_path: Path, files: dict) -> Path:
    for rel, text in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    owners._load.cache_clear()
    return tmp_path


@pytest.fixture
def tree(tmp_path):
    return _tree(tmp_path, {
        "OWNERS": "area: area:ci\nmodel: sonnet\nreview: bot\nprotected: false\nprotected_files: [pyproject.toml]\n",
        "lock/OWNERS": "area: area:release\nmodel: opus\nreview: opus\nprotected: true\n",
        "lock/inner/OWNERS": "area: area:docs\nprotected: false  # cannot unprotect\n",
        "app/OWNERS": "area: area:gui\nprotected: false\nprotected_files: [\"core/*.cfg\", \"**/secret.py\"]\n",
    })


def test_repo_owners_files_have_all_keys():
    for folder in REQUIRED:
        o = owners.parse((ROOT / folder / "OWNERS").read_text(encoding="utf-8"))
        assert {"area", "model", "review", "protected"} <= set(o), folder
        assert o["model"] in ("sonnet", "opus") and o["review"] in ("none", "bot", "opus"), folder


def test_new_folder_inherits_parent(tree):
    eff = owners.owners_for("app/brand_new/deeper/x.py", tree)
    assert eff == {"area": "area:gui", "model": "sonnet", "review": "bot", "protected": False}
    assert owners.owners_for("nowhere/x.py", tree)["area"] == "area:ci"  # root only
    eff = owners.owners_for("lock/inner/new/x.md", tree)
    assert eff["area"] == "area:docs" and eff["model"] == "opus"  # nearest key wins, missing keys inherited


def test_protection_is_union_along_chain(tree):
    assert owners.is_protected("lock/a.py", tree)
    assert owners.is_protected("lock/inner/new/x.md", tree)  # a nested `protected: false` never unprotects
    assert owners.owners_for("lock/inner/x.md", tree)["review"] == "opus"
    assert owners.is_protected("pyproject.toml", tree) and not owners.is_protected("app/pyproject.toml", tree)
    assert owners.is_protected("app/core/a.cfg", tree) and not owners.is_protected("app/core/sub/a.cfg", tree)
    assert owners.is_protected("app/secret.py", tree) and owners.is_protected("app/x/y/secret.py", tree)
    assert not owners.is_protected("app/core/a.py", tree)


def test_owners_files_protected_tests_not(tree):
    assert owners.is_protected("app/OWNERS", tree) and owners.is_protected("new/OWNERS", tree)
    _tree(tree, {"tests/OWNERS": "protected: true\n"})
    assert not owners.is_protected("tests/test_x.py", tree)  # section 3: tests/ never count


def test_fail_closed(tmp_path):
    _tree(tmp_path, {"a/OWNERS": "protected: true\n"})
    with pytest.raises(FileNotFoundError):
        owners.is_protected("a/x.py", tmp_path)
    for bad in ("protected: true\nprotected: false\n", "colour: red\n", "model: gpt\n", "review: maybe\n", "protected: yes\n"):
        with pytest.raises(ValueError):
            owners.parse(bad)


def test_merge_gate_reads_repo_owners():
    owners._load.cache_clear()
    assert is_protected("scripts/owners.py") and is_protected(".github/OWNERS") and is_protected("OWNERS")
    assert not is_protected("docs/design.md") and not is_protected("mobile/lib/main.dart")


def test_triage_uses_owners(tree):
    assert triage_issue.owners_hints("edit `lock/inner/a.md`", tree) == ("area:docs", "model:opus")
    assert triage_issue.owners_hints("`app/a.py` and `lock/b.py`", tree) == (None, "model:opus")  # areas disagree
    assert triage_issue.owners_hints("`nowhere/b.py`", tree) == (None, None)  # not a folder here
    assert triage_issue.owners_hints("no paths here", tree) == (None, None)
    owners._load.cache_clear()
    out = triage_issue.decide("[maint] tidy", "Why: a\nGoal: edit `.github/workflows/triage.yml`\nAcceptance: b\n"
                              "Verify: c", ["P2"], "OWNER")
    assert "area:ci" in out["add"] and "model:opus" in out["add"]
    out = triage_issue.decide("[docs] x", "Why: a\nGoal: `docs/design.md`\nAcceptance: b\nVerify: c",
                              ["P2"], "OWNER")
    assert "area:docs" in out["add"] and "model:sonnet" in out["add"]
