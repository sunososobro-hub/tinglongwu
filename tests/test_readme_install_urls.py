"""README one-line installs must point at the latest *release* assets, never at raw `main` scripts.

Release assets have the tag embedded (placeholder __TLWU_REF__), so they install exactly the released version;
raw main scripts fall back to unreleased `main`."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LATEST = "https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/"
RAW_MAIN = "https://raw.githubusercontent.com/sunososobro-hub/tinglongwu/main/scripts/"
ASSETS = {"install.sh", "mac_install.sh", "tinglongwu-win64.zip"}
DOCS = ("README.md", "README.zh-CN.md", "README.zh-TW.md", "docs/details.md", "docs/install.md",
        "docs/readme-full.zh-TW.md", "docs/readme-full.zh-CN.md")


def _urls(name):
    text = (ROOT / name).read_text("utf-8")
    return re.findall(r"https://[^\s`|\)]+", text)


def test_readmes_install_from_latest_release_assets():
    for name in ("README.md", "README.zh-CN.md", "README.zh-TW.md"):
        got = {u[len(LATEST):] for u in _urls(name) if u.startswith(LATEST)}
        assert ASSETS <= got, name


def test_release_workflow_publishes_the_linked_scripts():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    for asset in ("install.sh", "mac_install.sh"):
        assert f"dist/{asset}" in wf, asset


def test_no_doc_installs_from_raw_main():
    for name in DOCS:
        assert not any(u.startswith(RAW_MAIN) for u in _urls(name)), name


def test_install_table_is_at_the_top_of_the_readme():
    s = (ROOT / "README.md").read_text("utf-8")
    assert s.index(LATEST) < s.index("## ")
