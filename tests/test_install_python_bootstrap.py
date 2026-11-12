"""#67: install.ps1 provisions Python itself on a PC that has none. Runs the real PowerShell code (pwsh)."""
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell (pwsh) not installed")

PIN_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"


def ps(script: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                          cwd=cwd, capture_output=True, text=True, timeout=120)


def call(tmp_path: Path, sha: str, download: str, winget: str = "{ param($a) Write-Host 'WINGET-CALLED' }"):
    """Install-TlwuPython with fake download / installer / winget actions; the fake installer creates the target."""
    target = tmp_path / "py" / "python.exe"
    script = f"""
. '{ROOT / "scripts" / "python_bootstrap.ps1"}'
$r = Install-TlwuPython -Sha256 '{sha}' -Target '{target}' `
    -Download {download} `
    -RunInstaller {{ param($exe, $a) Write-Host ("INSTALLER-RAN " + ($a -join ' ')); New-Item -ItemType File -Force -Path '{target}' | Out-Null; 0 }} `
    -Winget {winget}
Write-Host "RESULT=[$r]"
"""
    return ps(script, tmp_path), target


FAKE = b"not really python"
WRITE_FAKE = "{ param($u, $o) Write-Host \"DOWNLOAD $u\"; [IO.File]::WriteAllBytes($o, [Text.Encoding]::ASCII.GetBytes('not really python')) }"


def test_verified_download_is_installed_per_user_without_admin(tmp_path):
    r, target = call(tmp_path, hashlib.sha256(FAKE).hexdigest(), WRITE_FAKE)
    assert r.returncode == 0, r.stderr
    assert f"DOWNLOAD {PIN_URL}" in r.stdout
    line = next(x for x in r.stdout.splitlines() if x.startswith("INSTALLER-RAN"))
    for arg in ("/quiet", "InstallAllUsers=0", "PrependPath=1"):
        assert arg in line.split()
    assert f"RESULT=[{target}]" in r.stdout
    assert "WINGET-CALLED" not in r.stdout


def test_sha256_mismatch_never_runs_the_download_and_falls_back_to_winget(tmp_path):
    r, target = call(tmp_path, "0" * 64, WRITE_FAKE)
    assert r.returncode == 0, r.stderr
    assert "INSTALLER-RAN" not in r.stdout
    assert "SHA256 mismatch" in r.stdout
    assert "WINGET-CALLED" in r.stdout
    assert "RESULT=[]" in r.stdout and not target.exists()


def test_download_failure_uses_winget(tmp_path):
    target = tmp_path / "py" / "python.exe"
    winget = ("{ param($a) Write-Host ('WINGET ' + ($a -join ' ')); "
              f"New-Item -ItemType File -Force -Path '{target}' | Out-Null }}")
    r, _ = call(tmp_path, "0" * 64, "{ param($u, $o) throw 'no network' }", winget)
    assert r.returncode == 0, r.stderr
    assert "INSTALLER-RAN" not in r.stdout
    assert "WINGET install -e --id Python.Python.3.12 --scope user" in r.stdout
    assert f"RESULT=[{target}]" in r.stdout


def test_pinned_hash_is_a_sha256_and_url_is_https_python_org(tmp_path):
    r = ps(f". '{ROOT / 'scripts' / 'python_bootstrap.ps1'}'; Write-Host \"$TlwuPyUrl|$TlwuPySha256\"", tmp_path)
    url, sha = r.stdout.strip().split("|")
    assert url == PIN_URL
    assert len(sha) == 64 and int(sha, 16) >= 0


def test_install_ps1_dry_run_without_python_plans_the_install_instead_of_exiting(tmp_path):
    """Before #67: "Python 3.12 not found", exit 1. Now: the plan is printed and the installer carries on."""
    work = tmp_path / "repo"
    work.mkdir()
    shutil.copy(ROOT / "install.ps1", work / "install.ps1")
    shutil.copytree(ROOT / "scripts", work / "scripts")   # install.ps1 dot-sources several helpers from here
    empty = tmp_path / "emptybin"
    empty.mkdir()
    r = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(work / "install.ps1"),
                        "-DryRun", "-SkipModels", "-SkipShortcut"],
                       cwd=work, capture_output=True, text=True, timeout=120,
                       env={"PATH": str(empty), "HOME": str(tmp_path), "TLWU_UI_LANG": "en"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"[dry-run] download {PIN_URL}" in r.stdout
    assert "InstallAllUsers=0" in r.stdout and "winget install" in r.stdout
    assert "[3/8]" in r.stdout and "-m venv" in r.stdout   # went on to the next steps with the planned python
    assert not (work / ".venv").exists()                    # dry run installs nothing
