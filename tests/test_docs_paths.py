from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_architecture_docs_point_to_committed_manifest():
    assert (ROOT / "tinglongwu_setup/manifest.json").is_file()
    for name in ("ARCHITECTURE.md", "ARCHITECTURE.zh-CN.md", "ARCHITECTURE.zh-TW.md"):
        s = (ROOT / "docs" / name).read_text("utf-8")
        assert "tinglongwu_setup/manifest.json" in s, name
        # the Whisper row must name the committed pin file, not only the local record
        row = next(line for line in s.splitlines() if "Systran" in line and line.startswith("|"))
        assert "tinglongwu_setup/manifest.json" in row, name


def test_privacy_statements_exist_and_readmes_link_them():
    import re
    for readme, target in (("README.md", "PRIVACY.md"), ("README.zh-TW.md", "PRIVACY.zh-TW.md")):
        links = re.findall(r"\]\(([^)#]+)\)", (ROOT / readme).read_text("utf-8"))
        assert target in links, readme
        assert (ROOT / target).is_file()
