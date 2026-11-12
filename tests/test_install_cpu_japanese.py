"""#126: OPUS has no usable Japanese, so the Windows installer's CPU tier also sets up Hy-MT2 (CPU llama-cpp-python
build + the GGUF) when the PC has a nominal 4 GB of RAM, and says plainly that Japanese is unsupported below that.

The PowerShell tests run the real helpers / `install.ps1 -DryRun` and need `pwsh` (preinstalled on the GitHub ubuntu
runners); they are skipped where it is missing."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tinglongwu import llamacheck

ROOT = Path(__file__).resolve().parent.parent
HELPERS = ROOT / "scripts" / "install_helpers.ps1"
PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh not installed")


def _ps(script: str, timeout: int = 120, **env) -> subprocess.CompletedProcess:
    e = {**os.environ, "POWERSHELL_TELEMETRY_OPTOUT": "1", "POWERSHELL_UPDATECHECK": "Off", **env}
    if "TLWU_TEST_RAM_MB" not in env:
        e.pop("TLWU_TEST_RAM_MB", None)
    return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                          text=True, encoding="utf-8", timeout=timeout, env=e, cwd=str(ROOT))


def _dry_run_cpu(**env) -> list[str]:
    r = _ps(f"& '{ROOT / 'install.ps1'}' -DryRun -Cpu -SkipShortcut; exit $LASTEXITCODE", timeout=300, **env)
    lines = r.stdout.splitlines()
    if r.returncode != 0 and any("Python 3.10-3.12 not found" in x for x in lines):
        pytest.skip("no Python 3.10-3.12 on PATH for the installer's step 1")
    assert r.returncode == 0, r.stdout + r.stderr
    return lines


@needs_pwsh
def test_cpu_japanese_needs_a_nominal_4_gb():
    r = _ps(f". '{HELPERS}'\n"
            "foreach ($m in 16384, 3800, 3584, 3583, 2048) { Write-Host (\"$m=\" + (Test-TlwuCpuJapanese $m)) }\n"
            "Write-Host ('unknown=' + (Test-TlwuCpuJapanese $null))\n"
            "$env:TLWU_TEST_RAM_MB = '3000'; Write-Host ('fake=' + (Get-TlwuRamMB))\n")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["16384=True", "3800=True", "3584=True", "3583=False", "2048=False",
                                     "unknown=True", "fake=3000"]


@needs_pwsh
def test_cpu_tier_installs_llama_cpu_build_and_hymt():
    lines = _dry_run_cpu(TLWU_TEST_RAM_MB="8192")
    pip = [x for x in lines if "[dry-run]" in x and "requirements-llama-cpu.txt" in x]
    assert len(pip) == 1 and "--only-binary=:all:" in pip[0]
    assert any("[dry-run]" in x and x.rstrip().endswith("--hymt") for x in lines)
    assert not any("requirements-qwen.txt" in x for x in lines)  # the CUDA wheel stays GPU-only
    assert any("translation=Hy-MT2 1.8B on the CPU" in x for x in lines)
    assert any("Translation engine / 翻譯引擎: hymt2" in x for x in lines)
    assert any(x.strip().startswith("Whole install / 全部安裝: 預估約 14-43 分鐘") for x in lines)  # +1.1 GB GGUF, +0.3 GB talk mode (#124)


@needs_pwsh
def test_low_ram_cpu_tier_still_installs_hymt_and_warns():
    """#301: Hy-MT2 is the only engine (#276), so a low-RAM PC gets it too, with a clear warning."""
    lines = _dry_run_cpu(TLWU_TEST_RAM_MB="3000")
    assert any("[dry-run]" in x and "requirements-llama-cpu.txt" in x for x in lines)
    assert any("[dry-run]" in x and x.rstrip().endswith("--hymt") for x in lines)
    warn = [x for x in lines if "the Hy-MT2 translator needs about 4 GB" in x]
    assert warn and "3000 MB" in warn[0]
    assert not any("OPUS engine" in x for x in lines)
    assert any(x.strip().startswith("Whole install / 全部安裝: 預估約 14-43 分鐘") for x in lines)


def test_cpu_requirements_pin_the_checked_llama_version_from_the_cpu_index():
    reqs = [ln.strip() for ln in (ROOT / "requirements-llama-cpu.txt").read_text("utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]
    assert reqs == [f"--extra-index-url {llamacheck.INDEX}/cpu", f"llama-cpp-python=={llamacheck.LLAMA_VERSION}"]
