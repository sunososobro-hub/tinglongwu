"""README one-line installs must point at raw main scripts that exist in scripts/ (releases/latest 404s: all releases are pre-releases)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = "https://raw.githubusercontent.com/sunososobro-hub/tinglongwu/main/scripts/"


def _urls(name):
    text = (ROOT / name).read_text("utf-8")
    return re.findall(r"https://[^\s`|\\)]+", text)


def test_readme_one_liners_use_existing_script_paths():
    for name in ("README.md", "README.zh-CN.md", "README.zh-TW.md"):
        urls = [u for u in _urls(name) if u.startswith(RAW)]
        got = {u[len(RAW):] for u in urls}
        assert {"install.sh", "mac_install.sh", "bootstrap_windows.ps1"} <= got, name
        for rel in got:
            assert (ROOT / "scripts" / rel).is_file(), (name, rel)


def test_no_doc_points_at_releases_latest_download():
    for name in ("README.md", "README.zh-CN.md", "README.zh-TW.md", "docs/install.md"):
        assert not any("releases/latest/download" in u for u in _urls(name)), name
