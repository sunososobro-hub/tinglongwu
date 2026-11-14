"""#755: run_install.ps1 must not hold install.log open (the wizard reads it every poll) and must write it BOM-free."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh") or shutil.which("powershell")

pytestmark = pytest.mark.skipif(PWSH is None, reason="needs PowerShell (runs in the Windows CI jobs)")


def test_install_log_is_bomfree_complete_and_not_held_open(tmp_path):
    (tmp_path / "installer").mkdir()
    shutil.copy(ROOT / "installer" / "run_install.ps1", tmp_path / "installer" / "run_install.ps1")
    # stub install.ps1: prints step lines, then appends to install.log itself while still "running" -
    # that append only works if run_install.ps1 is not holding the file open exclusively
    (tmp_path / "install.ps1").write_text(
        "param([switch]$SkipShortcut,[switch]$FromSetup,[switch]$Cpu)\n"
        "Write-Host '[1/8] one'\nWrite-Host '[2/8] zwei 測試'\n"
        "[IO.File]::AppendAllText((Join-Path $PSScriptRoot 'install.log'), \"probe`r`n\")\n"
        "Write-Host '[3/8] three'\n",
        encoding="utf-8-sig",
    )
    r = subprocess.run([PWSH, "-NoProfile", "-File", str(tmp_path / "installer" / "run_install.ps1")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    raw = (tmp_path / "install.log").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and not raw.startswith(b"\xff\xfe")
    lines = raw.decode("utf-8").splitlines()
    assert [ln for ln in lines if ln.startswith("[")] == ["[1/8] one", "[2/8] zwei 測試", "[3/8] three"]
    assert "probe" in lines
    assert (tmp_path / "install.done").read_text().strip() == "0"
