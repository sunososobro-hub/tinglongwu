"""#690: docs consistency (privacy wording, language-pair table, contributor docs)."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(p):
    return (ROOT / p).read_text("utf-8")


def test_no_recordings_wording_says_by_default():
    for p in ("README.md", "docs/VISION.md"):
        for line in _read(p).splitlines():
            if re.search(r"no recordings kept", line, re.I):
                assert "by default" in line, (p, line)


def test_language_pair_table_matches_support_json():
    block = re.search(r"<!-- LANGPAIRS:START -->\n(.*?)\n<!-- LANGPAIRS:END -->", _read("docs/languages.md"), re.S).group(1)
    rows = [[c.strip() for c in ln.strip("|").split("|")] for ln in block.splitlines()[2:]]
    src = json.loads(_read("docs/support.json"))["language"]["rows"]
    assert [r[0] for r in rows] == [r["label"] for r in src]
    assert [r[1:] for r in rows] == [r["cells"][:4] for r in src]


def test_single_dev_install_path_and_coc():
    assert not (ROOT / "requirements-dev.txt").exists()
    for p in ("docs/testing.md", "docs/readme-full.zh-CN.md", "docs/readme-full.zh-TW.md", "CONTRIBUTING.md"):
        assert "requirements-dev" not in _read(p).replace("old `requirements-dev.txt` is gone", "") , p
    c = _read("CONTRIBUTING.md")
    assert "mobile/README.md" in c and "CODE_OF_CONDUCT.md" in c and "`needs-opus`" in c
    assert c.count("default answer is yes") == 1
    assert (ROOT / "CODE_OF_CONDUCT.md").exists() and (ROOT / "docs/history-rewrite.md").exists()
