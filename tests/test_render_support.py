"""The README support/contributors block and the release notes come from one source (docs/support.json)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location("render_support", ROOT / "scripts" / "render_support.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_readme_blocks_match_the_source():
    assert _mod().main(["--check"]) == 0, "run: python scripts/render_support.py"


def test_release_notes_use_the_same_block_as_the_english_readme():
    m = _mod()
    src, people = m.load()
    readme = (ROOT / "README.md").read_text("utf-8")
    block = m.BLOCK.search(readme).group(0)
    notes = m.notes("v9.9.9", src, people)
    assert block in notes
    assert notes.index("## Download") < notes.index(block) < notes.index("## What's new")
    assert "releases/download/v9.9.9/tinglongwu-setup-9.9.9.exe" in notes


def test_every_contributor_and_type_is_rendered():
    m = _mod()
    src, people = m.load()
    html = m.contributors(src, people, "en")
    assert f"Built with help from {len(people)} people" in html
    for p in people:
        assert f"https://github.com/{p['login']}.png?size=60" in html
    assert "@" not in html.replace("://", "")


def test_check_detects_a_stale_readme(tmp_path):
    m = _mod()
    for name in ("README.md", "README.zh-TW.md", "README.zh-CN.md", ".all-contributorsrc"):
        (tmp_path / name).write_text((ROOT / name).read_text("utf-8"), "utf-8")
    (tmp_path / "docs").mkdir()
    src = json.loads((ROOT / "docs" / "support.json").read_text("utf-8"))
    src["language"]["rows"][-1]["cells"][0] = "🟢"
    (tmp_path / "docs" / "support.json").write_text(json.dumps(src, ensure_ascii=False), "utf-8")
    assert m.main(["--check"], root=tmp_path) == 1
    assert m.main([], root=tmp_path) == 0 and m.main(["--check"], root=tmp_path) == 0


def test_release_workflow_writes_the_notes_from_the_source():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    assert 'scripts/render_support.py --notes "$TAG" > dist/notes.md' in wf
    assert "--notes-file dist/notes.md" in wf


def test_auto_publish_waits_for_the_whats_new_todo():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    m = _mod()
    assert "TODO:" in m.notes("v1.0.0", *m.load())
    i = wf.index("grep -q 'TODO:'")
    assert i < wf.index("--draft=false", i)


def test_notes_file_accepts_a_bom(tmp_path):
    m = _mod()
    f = tmp_path / "notes.md"
    f.write_text("﻿## Download\n\n<!-- SUPPORT:START -->old<!-- SUPPORT:END -->\n", "utf-8")
    assert m.main(["--notes-file", str(f)]) == 0
    s = f.read_text("utf-8")
    assert s.startswith("## Download") and "Built with help from" in s
