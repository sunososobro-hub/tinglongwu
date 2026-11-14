"""#341: scripts/win_smoke.ps1 passes only when `--smoke` exits 0 and the GUI stays up for N seconds.
Runs the real script against fake tlwu.cmd apps (Windows PowerShell only)."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PS1 = Path(__file__).resolve().parents[1] / "scripts" / "win_smoke.ps1"
pytestmark = pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell"),
                                reason="needs Windows PowerShell")


def _run(tmp_path, cmd_body):
    app = tmp_path / "app"
    app.mkdir()
    (app / "tlwu.cmd").write_text("@echo off\r\n" + cmd_body, encoding="ascii")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS1),
                        "-AppDir", str(app), "-ExeName", "tlwu.cmd", "-GuiSeconds", "3",
                        "-LogDir", str(tmp_path / "logs")], capture_output=True, text=True, timeout=120)
    return r, tmp_path / "logs"


def test_ok_when_smoke_passes_and_gui_stays_up(tmp_path):
    r, logs = _run(tmp_path, "if \"%1\"==\"--smoke\" (echo smoke: OK& exit /b 0)\r\nping -n 30 127.0.0.1 >nul\r\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (logs / "smoke.out.txt").exists() and (logs / "gui.out.txt").exists()


def test_smoke_failure_is_red(tmp_path):
    r, _ = _run(tmp_path, "echo missing DLL\r\nexit /b 1\r\n")
    assert r.returncode == 1, r.stdout + r.stderr


def test_gui_crash_is_red(tmp_path):
    r, _ = _run(tmp_path, "if \"%1\"==\"--smoke\" exit /b 0\r\nexit /b 5\r\n")
    assert r.returncode == 2, r.stdout + r.stderr
