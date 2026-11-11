"""#138: the Linux installer must not accept Python 3.10/3.11 (the pinned numpy 2.5.4 / scipy 1.18.1 need >= 3.12).

Runs the real scripts/install.sh with a fake PATH where only Python 3.11 exists; every outside effect (id, apt,
sudo, git, curl, dpkg) is a stub, so nothing is installed or downloaded.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="needs bash")


def _stub(d: Path, name: str, body: str) -> None:
    p = d / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)


def _fake_python(d: Path, name: str, version: tuple[int, int]) -> None:
    # answers the installer's `-c '<version check>'` and `--version` like a real interpreter of that version
    code = f"import sys; sys.version_info = ({version[0]}, {version[1]}, 0); exec(sys.argv[-1])"
    _stub(d, name, f'''if [ "$1" = "--version" ]; then echo "Python {version[0]}.{version[1]}.0"; exit 0; fi
if [ "$1" = "-c" ]; then exec "{sys.executable}" -c "{code}" "$2"; fi
exit 1''')


def _run(tmp_path: Path, pythons: dict[str, tuple[int, int]]):
    fake = tmp_path / "bin"
    fake.mkdir()
    for name, ver in pythons.items():
        _fake_python(fake, name, ver)
    for name in ("python3", "python3.12", "python3.11", "python3.10", "python3.13"):
        if name not in pythons:  # shadow any real interpreter on the system PATH: "not this version"
            _stub(fake, name, "exit 1")
    _stub(fake, "id", 'echo 1000')
    _stub(fake, "dpkg", "exit 0")  # audio libs "installed"
    _stub(fake, "apt-get", "exit 0")
    _stub(fake, "sudo", "exit 0")  # apt "succeeds" but cannot change which Python exists
    _stub(fake, "curl", "exit 0")
    _stub(fake, "git", 'echo "git $*" >> "$TLWU_LOG"; exit 1')  # reaching git = the Python check passed
    env = {"PATH": f"{fake}:/usr/bin:/bin", "HOME": str(tmp_path), "TLWU_DIR": str(tmp_path / "tlwu"),
           "TLWU_UI_LANG": "en", "TLWU_LOG": str(tmp_path / "git.log")}
    r = subprocess.run(["bash", str(ROOT / "scripts" / "install.sh")], input="y\n", capture_output=True, text=True,
                       env=env, timeout=60)
    return r, (tmp_path / "git.log")


def test_python_311_only_is_refused_before_anything_is_cloned(tmp_path):
    r, git_log = _run(tmp_path, {"python3.11": (3, 11), "python3": (3, 11)})
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "python3 must be 3.12 (found Python 3.11.0)" in out
    assert not git_log.exists(), "installer went on to git clone with Python 3.11"


def test_python_310_only_is_refused(tmp_path):
    r, git_log = _run(tmp_path, {"python3.10": (3, 10), "python3": (3, 10)})
    assert r.returncode == 1
    assert "python3 must be 3.12" in r.stdout + r.stderr
    assert not git_log.exists()


def test_python_312_is_accepted(tmp_path):
    r, git_log = _run(tmp_path, {"python3.12": (3, 12), "python3": (3, 11)})
    assert "must be 3.12" not in r.stdout + r.stderr
    assert git_log.exists(), r.stdout + r.stderr  # went past the Python check to the clone step

