"""#1678: install.sh writes one tinglongwu.desktop app shortcut; uninstall.sh removes it. Real scripts, temp HOME."""
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


def _install_shortcut(h: Path, d: Path) -> None:
    code = 'TLWU_SOURCE_ONLY=1 source "$S"; DIR="$D"; install_desktop_shortcut'
    r = subprocess.run(["bash", "-c", code], env={**_env(h), "S": str(ROOT / "scripts" / "install.sh"), "D": str(d)},
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def test_desktop_shortcut_written_once_and_uninstalled(tmp_path):
    h = tmp_path / "home"
    d = h / "tinglongwu"
    (d / "scripts").mkdir(parents=True)
    (d / ".git").mkdir()
    (d / "scripts" / "linux_menu.sh").write_text("")
    shutil.copy(ROOT / "scripts" / "uninstall.sh", d / "scripts" / "uninstall.sh")
    _install_shortcut(h, d)
    _install_shortcut(h, d)
    apps = h / ".local" / "share" / "applications"
    files = sorted(p.name for p in apps.iterdir())
    assert files == ["tinglongwu.desktop"]
    text = (apps / "tinglongwu.desktop").read_text()
    assert f"Exec={d}/.venv/bin/tlwu-gui\n" in text
    assert f"Icon={d}/src/ui/tinglongwu/gui/assets/tinglongwu.png\n" in text
    assert "Terminal=false\n" in text
    r = subprocess.run(["bash", str(d / "scripts" / "uninstall.sh")], env=_env(h), stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert not (apps / "tinglongwu.desktop").exists()


def test_uninstall_without_shortcut_is_fine_and_keeps_foreign_desktop_file(tmp_path):
    h = tmp_path / "home"
    d = h / "tinglongwu"
    (d / "scripts").mkdir(parents=True)
    (d / "scripts" / "linux_menu.sh").write_text("")
    apps = h / ".local" / "share" / "applications"
    apps.mkdir(parents=True)
    (apps / "tinglongwu.desktop").write_text("[Desktop Entry]\nName=Someone else\n")
    r = subprocess.run(["bash", str(ROOT / "scripts" / "uninstall.sh")], env={**_env(h), "TLWU_DIR": str(d)},
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert (apps / "tinglongwu.desktop").exists()
