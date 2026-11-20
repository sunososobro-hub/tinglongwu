"""#1534: mobile versionName / dev label / release-notes helpers (mobile/tool/mobile_version.py)."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "mobile" / "tool" / "mobile_version.py"
sys.path.insert(0, str(SCRIPT.parent))
import mobile_version as mv  # noqa: E402


def test_version_name_strips_build_number():
    assert mv.version_name("name: m\nversion: 1.0.7+42\n") == "1.0.7"
    assert mv.version_name("version: 1.2.0\n") == "1.2.0"


def test_version_name_rejects_garbage():
    with pytest.raises(ValueError):
        mv.version_name("name: m\n")
    with pytest.raises(ValueError):
        mv.version_name("version: 1.0\n")


def test_dev_name_and_next_patch():
    assert mv.dev_name("1.0.0", 155) == "1.0.0-dev.155"
    assert mv.next_patch("1.0.9") == "1.0.10"
    for bad in (0, "x", -3):
        with pytest.raises(ValueError):
            mv.dev_name("1.0.0", bad)


NOTES = """# What's new

## mobile 1.0.1
- Russian subtitles now appear faster.
- Conversation mode keeps your history.

## mobile 1.0.0
- First release.

## mobile 1.0.2
- TODO fill in
"""


def test_notes_section_returns_only_that_section():
    body = mv.notes_section(NOTES, "1.0.1")
    assert "Russian subtitles" in body and "First release" not in body


@pytest.mark.parametrize("ver", ["9.9.9", "1.0.2"])
def test_notes_section_missing_or_todo_fails(ver):
    with pytest.raises(ValueError):
        mv.notes_section(NOTES, ver)


def test_cli_exit_codes(tmp_path):
    pub = tmp_path / "pubspec.yaml"
    pub.write_text("version: 1.0.3+9\n")
    r = subprocess.run([sys.executable, "-I", str(SCRIPT), "dev", "77", str(pub)],
                       capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "1.0.3-dev.77"
    wn = tmp_path / "wn.md"
    wn.write_text(NOTES)
    r = subprocess.run([sys.executable, "-I", str(SCRIPT), "notes", "7.7.7", str(wn)],
                       capture_output=True, text=True)
    assert r.returncode == 1
    real = subprocess.run([sys.executable, "-I", str(SCRIPT), "name"], capture_output=True, text=True)
    assert real.returncode == 0 and real.stdout.strip().count(".") == 2


def test_default_version_follows_desktop_dunder_version(tmp_path):
    """#1598: no path argument -> versionName is tinglongwu.__version__, not pubspec.yaml."""
    import re
    init = (ROOT / "src" / "core" / "tinglongwu" / "__init__.py").read_text(encoding="utf-8")
    want = re.search(r'^__version__\s*=\s*"([^"]+)"', init, re.M).group(1)
    r = subprocess.run([sys.executable, "-I", str(SCRIPT), "name"], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == want
    r = subprocess.run([sys.executable, "-I", str(SCRIPT), "dev", "12"], capture_output=True, text=True)
    assert r.stdout.strip() == want + "-dev.12"
    fake = tmp_path / "__init__.py"
    fake.write_text('"""x"""\n__version__ = "0.9.4"\n')
    r = subprocess.run([sys.executable, "-I", str(SCRIPT), "next", str(fake)], capture_output=True, text=True)
    assert r.stdout.strip() == "0.9.5"


def test_desktop_version_rejects_garbage():
    assert mv.desktop_version('__version__ = "0.4.11"\n') == "0.4.11"
    with pytest.raises(ValueError):
        mv.desktop_version('__version__ = "dev"\n')
