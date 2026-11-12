"""#87: the Windows installer must never sit silent. Estimates per step + up front, native commands (pip, tlwu-setup)
stream to the console instead of being captured, and direct model downloads print a percentage.

The PowerShell tests need `pwsh` (preinstalled on the GitHub ubuntu runners); they are skipped where it is missing."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tinglongwu_setup import downloader as dl

ROOT = Path(__file__).resolve().parent.parent
HELPERS = ROOT / "scripts" / "install_helpers.ps1"
PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh not installed")


def _ps(script: str, timeout: int = 120) -> subprocess.CompletedProcess:
    env = {**os.environ, "POWERSHELL_TELEMETRY_OPTOUT": "1", "POWERSHELL_UPDATECHECK": "Off"}
    return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                          text=True, encoding="utf-8", timeout=timeout, env=env, cwd=str(ROOT))


# ---------------------------------------------------------------- Python: download percent

class _Resp(io.BytesIO):
    def __init__(self, data: bytes, length: str | None):
        super().__init__(data)
        self.headers = {"Content-Length": length} if length is not None else {}


def test_copy_with_progress_prints_percent(capsys):
    data = b"x" * (10 * dl.MB)
    out = io.BytesIO()
    r = _Resp(data, str(len(data)))
    n = dl.copy_with_progress(r, out, dl._size(r), "download opus-ru-en", step=25)
    assert n == len(data) and out.getvalue() == data
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["download opus-ru-en: 30% (3/10 MB)", "download opus-ru-en: 50% (5/10 MB)",
                     "download opus-ru-en: 80% (8/10 MB)", "download opus-ru-en: 100% (10/10 MB)"]


def test_copy_with_progress_unknown_size_prints_megabytes(capsys):
    r = _Resp(b"y" * (120 * dl.MB), None)
    assert dl._size(r) is None and dl._size(io.BytesIO()) is None
    dl.copy_with_progress(r, io.BytesIO(), None, "dl")
    assert capsys.readouterr().out.splitlines() == ["dl: 50 MB", "dl: 100 MB"]


# ---------------------------------------------------------------- PowerShell helpers

@needs_pwsh
def test_native_output_streams_and_return_value_is_only_the_exit_code(tmp_path):
    prog = tmp_path / "echo args.py"
    prog.write_text("import sys, json\nprint('PIP-LINE ' + json.dumps(sys.argv[1:]))\nsys.exit(int(sys.argv[1]))\n")
    args = ["0", "a b", 'say "hi"', "C:\\dir with space\\", "", "plain"]
    ps_args = ", ".join("'" + a.replace("'", "''") + "'" for a in args)
    r = _ps(f". '{HELPERS}'\n"
            f"$rc = Invoke-TlwuNative '{sys.executable}' @('{prog}', {ps_args})\n"
            "Write-Host ('RC=' + $rc.GetType().Name + ':' + $rc)\n"
            f"$rc = Invoke-TlwuNative '{sys.executable}' @('{prog}', '3')\n"
            "Write-Host ('RC=' + $rc.GetType().Name + ':' + $rc)\n")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    # the child's own line reached the console (not swallowed), with every argument intact
    assert "PIP-LINE " + json.dumps(args) in lines
    # the function returns just the exit code, not the child's output
    assert "RC=Int32:0" in lines and "RC=Int32:3" in lines


@needs_pwsh
def test_estimates_per_step_and_total():
    r = _ps(f". '{HELPERS}'\n"
            "Write-Host (Format-TlwuEstimate (Get-TlwuStepEstimate 1 $false 1))\n"
            "Write-Host (Format-TlwuEstimate (Get-TlwuStepEstimate 3 $false 1))\n"
            "Write-Host (Format-TlwuEstimate (Get-TlwuStepEstimate 4 $false 1))\n"
            "Write-Host (Format-TlwuEstimate (Get-TlwuStepEstimate 4 $true 10))\n"
            "Write-Host (Format-TlwuEstimate (Get-TlwuStepEstimate 5 $true 10))\n"
            "Write-Host (Format-TlwuEstimate (Get-TlwuStepEstimate 6 $true 10))\n"
            "Write-Host (Format-TlwuEstimate (Get-TlwuStepEstimate 6 $true 0))\n"
            "Write-Host (Format-TlwuEstimate (Get-TlwuTotalEstimate $false 1))\n"
            "Write-Host (Format-TlwuEstimate (Get-TlwuTotalEstimate $true 10))\n")
    assert r.returncode == 0, r.stderr
    got = r.stdout.splitlines()
    assert got[0] == got[2] == got[6] == "預估不到 1 分鐘 / under 1 minute"  # step 6 with -SkipModels (0 GB)
    # 3 GB pkgs, 1.5 GB GPU libs, 10 GB models, 0.3 GB talk mode (#124), totals
    want = ["7-23", "4-12", "15-70", "2-4", "13-36", "29-110"]
    for line, w in zip([got[1], got[3], got[4], got[5], got[7], got[8]], want):
        assert line == f"預估約 {w} 分鐘（視網路速度） / about {w} min, depending on your connection"


@needs_pwsh
def test_install_dry_run_prints_an_estimate_under_every_step():
    r = _ps(f"& '{ROOT / 'install.ps1'}' -DryRun -Cpu -SkipShortcut; exit $LASTEXITCODE", timeout=300)
    lines = r.stdout.splitlines()
    if r.returncode != 0 and any("Python 3.10-3.12 not found" in x for x in lines):
        pytest.skip("no Python 3.10-3.12 on PATH for the installer's step 1")
    assert r.returncode == 0, r.stdout + r.stderr
    steps = [i for i, x in enumerate(lines) if x.startswith("[") and "/8] " in x]
    assert len(steps) == 8
    for i in steps:
        assert "預估" in lines[i + 1] and ("min" in lines[i + 1] or "minute" in lines[i + 1])
    # CPU tier with RAM unknown/enough: ~1 GB models + Hy-MT2 for Japanese (#126) -> 2 GB
    assert any(x.strip().startswith("Whole install / 全部安裝: 預估約 14-43 分鐘") for x in lines)
