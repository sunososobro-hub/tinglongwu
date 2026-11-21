"""#1679: mac_install.sh writes one ~/Applications/tinglongWu.app launcher; uninstall.sh removes it. Real scripts, temp HOME."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="needs bash")


def _env(h: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TLWU_", "KOKORO_"))}
    env.update(HOME=str(h), TLWU_YES="1")
    return env


def _install_launcher(h: Path, d: Path) -> None:
    code = 'TLWU_SOURCE_ONLY=1 source "$S"; DIR="$D"; install_app_launcher'
    r = subprocess.run(["bash", "-c", code], env={**_env(h), "S": str(ROOT / "scripts" / "mac_install.sh"), "D": str(d)},
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def _home(tmp_path):
    h = tmp_path / "home"
    d = h / "tinglongwu"
    (d / "scripts").mkdir(parents=True)
    (d / "scripts" / "linux_menu.sh").write_text("")
    shutil.copy(ROOT / "scripts" / "uninstall.sh", d / "scripts" / "uninstall.sh")
    return h, d


def test_mac_launcher_written_once_and_uninstalled(tmp_path):
    h, d = _home(tmp_path)
    _install_launcher(h, d)
    _install_launcher(h, d)
    apps = h / "Applications"
    assert sorted(p.name for p in apps.iterdir()) == ["tinglongWu.app"]
    exe = apps / "tinglongWu.app" / "Contents" / "MacOS" / "tinglongwu"
    assert os.access(exe, os.X_OK)
    assert f"exec {d}/.venv/bin/tlwu-gui " in exe.read_text()
    assert (apps / "tinglongWu.app" / "Contents" / "Info.plist").is_file()
    r = subprocess.run(["bash", str(d / "scripts" / "uninstall.sh")], env=_env(h), stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert not (apps / "tinglongWu.app").exists()


def test_uninstall_keeps_foreign_app_bundle(tmp_path):
    h, d = _home(tmp_path)
    exe = h / "Applications" / "tinglongWu.app" / "Contents" / "MacOS" / "tinglongwu"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/bash\necho mine\n")
    r = subprocess.run(["bash", str(d / "scripts" / "uninstall.sh")], env={**_env(h), "TLWU_DIR": str(d)},
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert exe.exists()


def test_install_refuses_to_replace_foreign_bundle(tmp_path):
    h, d = _home(tmp_path)
    exe = h / "Applications" / "tinglongWu.app" / "Contents" / "MacOS" / "tinglongwu"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/bash\necho mine\n")
    code = 'TLWU_SOURCE_ONLY=1 source "$S"; DIR="$D"; install_app_launcher'
    r = subprocess.run(["bash", "-c", code], env={**_env(h), "S": str(ROOT / "scripts" / "mac_install.sh"), "D": str(d)},
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert "echo mine" in exe.read_text()


def test_launcher_path_with_dollar_is_literal(tmp_path):
    h, d = _home(tmp_path)
    d = h / "tlwu$variant"
    d.mkdir()
    _install_launcher(h, d)
    exe = h / "Applications" / "tinglongWu.app" / "Contents" / "MacOS" / "tinglongwu"
    gui = d / ".venv" / "bin" / "tlwu-gui"
    gui.parent.mkdir(parents=True)
    gui.write_text("#!/bin/bash\necho ran-gui\n")
    gui.chmod(0o755)
    r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
    assert r.stdout.strip() == "ran-gui"
