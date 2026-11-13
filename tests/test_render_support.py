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
    assert m.C_BLOCK.search(readme).group(0) == m.readme_block(src, people, "en")
    assert m.START not in readme and "latency.svg" not in readme  # support tables live in the notes and on the roadmap
    notes = m.notes("v9.9.9", src, people)
    block = m.block(src, people, "en")
    assert block in notes and m.contributors(src, people, "en") in block
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
    (tmp_path / "docs").mkdir(exist_ok=True)
    for name in ("README.md", "README.zh-TW.md", "README.zh-CN.md", ".all-contributorsrc", "docs/roadmap.html"):
        (tmp_path / name).write_text((ROOT / name).read_text("utf-8"), "utf-8")
    (tmp_path / "docs").mkdir(exist_ok=True)
    src = json.loads((ROOT / "docs" / "support.json").read_text("utf-8"))
    src["contributors"]["heading"]["en"] = "Thanks to {n} people"
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
    f.write_text("\ufeff## Download\n\n<!-- SUPPORT:START -->old<!-- SUPPORT:END -->\n", "utf-8")
    assert m.main(["--notes-file", str(f)]) == 0
    s = f.read_text("utf-8")
    assert s.startswith("## Download") and "Built with help from" in s


def test_roadmap_grid_is_generated_from_support_json():
    m = _mod()
    src, _ = m.load()
    html = (ROOT / "docs" / "roadmap.html").read_text("utf-8")
    assert m.GRID.search(html).group(0) == m.roadmap_grid(src)
    grid = m.roadmap_grid(src)
    assert grid.count('class="r"') == len(src["device"]["rows"]) + len(src["language"]["rows"])
    # one dot or dash per cell
    n = sum(len(r["cells"]) for r in src["device"]["rows"] + src["language"]["rows"])
    assert grid.count('class="c"') + grid.count('class="c same"') == n
    assert "tinglongwu#readme" in html  # links to the README support section


def test_roadmap_check_detects_a_stale_grid(tmp_path):
    m = _mod()
    for name in ("README.md", "README.zh-TW.md", "README.zh-CN.md", ".all-contributorsrc", "docs/roadmap.html"):
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text((ROOT / name).read_text("utf-8"), "utf-8")
    src = json.loads((ROOT / "docs" / "support.json").read_text("utf-8"))
    src["device"]["rows"][0]["cells"][0] = "🟢"
    (tmp_path / "docs" / "support.json").write_text(json.dumps(src, ensure_ascii=False), "utf-8")
    assert m.main(["--check"], root=tmp_path) == 1


def test_roadmap_next_release_card_is_generated_from_support_json():
    m = _mod()
    src, _ = m.load()
    html = (ROOT / "docs" / "roadmap.html").read_text("utf-8")
    assert m.NEXT.search(html).group(0) == m.roadmap_next(src)
    assert src["next_release"]["milestone"] in html
    assert "Meeting mode tells you and the other side apart" in html


def test_pages_home_links_to_the_roadmap():
    src = (ROOT / "scripts" / "stats_dashboard.py").read_text("utf-8")
    assert 'href="roadmap.html"' in src
