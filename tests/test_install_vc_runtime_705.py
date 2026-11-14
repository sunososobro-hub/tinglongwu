"""#705: a clean Windows 11 has no Microsoft Visual C++ 2015-2022 runtime, so llama.dll cannot load. install.ps1 step 4
installs the runtime first (signature-checked), and stops before the 2 GB model download when the engine still cannot
load.

The helper tests run the real functions of scripts/install_helpers.ps1 and need `pwsh` (preinstalled on the GitHub
ubuntu runners); they are skipped where it is missing. The last tests pin where install.ps1 and the wizard call them."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HELPERS = ROOT / "scripts" / "install_helpers.ps1"
INSTALL = (ROOT / "install.ps1").read_text("utf-8-sig")
ISS = (ROOT / "installer" / "tinglongwu.iss").read_text("utf-8-sig")
PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh not installed")

MS = "CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US"
ALL_DLLS = ["msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll", "vcomp140.dll"]


def _ps(script: str, timeout: int = 120) -> dict[str, str]:
    """Run `script` after dot-sourcing the helpers; returns the `KEY=value` lines it printed."""
    env = {**os.environ, "POWERSHELL_TELEMETRY_OPTOUT": "1", "POWERSHELL_UPDATECHECK": "Off"}
    r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", f". '{HELPERS}'\n{script}"],
                       capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=env, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    return {k: v for k, _, v in (ln.partition("=") for ln in r.stdout.splitlines()) if k.isupper()}


@needs_pwsh
def test_missing_dlls_lists_exactly_the_absent_runtime_files(tmp_path):
    out = _ps(f"Write-Host ('NONE=' + ((Get-TlwuMissingVcDlls '{tmp_path}') -join ','))\n"
              + "\n".join(f"New-Item -ItemType File -Path (Join-Path '{tmp_path}' '{n}') | Out-Null"
                          for n in ALL_DLLS[:3])
              + f"\nWrite-Host ('ONE=' + ((Get-TlwuMissingVcDlls '{tmp_path}') -join ','))\n"
              f"New-Item -ItemType File -Path (Join-Path '{tmp_path}' '{ALL_DLLS[3]}') | Out-Null\n"
              f"Write-Host ('ALL=' + @(Get-TlwuMissingVcDlls '{tmp_path}').Count)")
    assert out["NONE"] == ",".join(ALL_DLLS)
    assert out["ONE"] == "vcomp140.dll"  # one missing file is enough to need the runtime (llama.dll loads vcomp140)
    assert out["ALL"] == "0"


@needs_pwsh
def test_only_a_valid_microsoft_corporation_signature_is_accepted():
    cases = {
        "VALID": ("Valid", MS),
        "HASH": ("HashMismatch", MS),  # tampered download
        "NOTSIGNED": ("NotSigned", None),
        "OTHER": ("Valid", "CN=Evil Ltd, O=Evil Ltd, C=US"),
        "LOOKALIKE": ("Valid", "CN=x, O=Microsoft Corporation Evil, C=US"),
    }
    lines = []
    for key, (status, subject) in cases.items():
        cert = "$null" if subject is None else f"[pscustomobject]@{{ Subject = '{subject}' }}"
        lines.append(f"Write-Host ('{key}=' + (Test-TlwuMicrosoftSignature "
                     f"([pscustomobject]@{{ Status = '{status}'; SignerCertificate = {cert} }})))")
    lines.append("Write-Host ('NULL=' + (Test-TlwuMicrosoftSignature $null))")
    out = _ps("\n".join(lines))
    assert out == {"VALID": "True", "HASH": "False", "NOTSIGNED": "False", "OTHER": "False", "LOOKALIKE": "False",
                   "NULL": "False"}


def _install(fetch: str, sign_status: str = "Valid", sign_subject: str = MS, exit_code: int = 0) -> dict[str, str]:
    """Install-TlwuVcRuntime with the download / signature / run stubbed; reports the call log and the result."""
    return _ps(
        "$global:Log = @()\n"
        f"$fetch = {{ param($u, $f) $global:Log += ('fetch ' + $u); {fetch} }}\n"
        f"$sign = {{ param($f) $global:Log += 'sign'; [pscustomobject]@{{ Status = '{sign_status}'; "
        f"SignerCertificate = [pscustomobject]@{{ Subject = '{sign_subject}' }} }} }}\n"
        f"$exec = {{ param($f, $a) $global:Log += ('exec ' + ($a -join ' ')); {exit_code} }}\n"
        "$ok = Install-TlwuVcRuntime -Fetch $fetch -Sign $sign -Exec $exec\n"
        "Write-Host ('RESULT=' + $ok)\n"
        "Write-Host ('LOG=' + ($global:Log -join ' | '))\n"
        "Write-Host ('LEFTOVER=' + (Test-Path (Join-Path ([IO.Path]::GetTempPath()) 'tlwu_vc_redist.x64.exe')))")


WRITE = "Set-Content -LiteralPath $f -Value 'MZ'"


@needs_pwsh
def test_signed_download_is_run_quiet_without_restart_and_the_file_is_removed():
    out = _install(WRITE)
    assert out["RESULT"] == "True" and out["LEFTOVER"] == "False"
    assert out["LOG"] == "fetch https://aka.ms/vs/17/release/vc_redist.x64.exe | sign | exec /install /quiet /norestart"


@needs_pwsh
@pytest.mark.parametrize("status,subject", [("HashMismatch", MS), ("NotSigned", MS), ("Valid", "CN=Evil, O=Evil, C=US")])
def test_a_download_without_a_valid_microsoft_signature_is_never_run(status, subject):
    out = _install(WRITE, sign_status=status, sign_subject=subject)
    assert out["RESULT"] == "False" and out["LEFTOVER"] == "False"
    assert "exec" not in out["LOG"]


@needs_pwsh
@pytest.mark.parametrize("code,ok", [(0, "True"), (3010, "True"), (1638, "True"), (1603, "False"), (1602, "False")])
def test_exit_codes_done_reboot_pending_and_newer_installed_count_as_installed(code, ok):
    assert _install(WRITE, exit_code=code)["RESULT"] == ok


@needs_pwsh
def test_a_failed_or_empty_download_returns_false_without_signature_check_or_run():
    out = _install("throw 'no network'")
    assert out["RESULT"] == "False" and out["LOG"] == "fetch https://aka.ms/vs/17/release/vc_redist.x64.exe"
    out = _install("")  # the download "succeeds" but writes no file
    assert out["RESULT"] == "False" and "sign" not in out["LOG"] and "exec" not in out["LOG"]


# ---------------------------------------------------------------- where install.ps1 and the wizard use it
def _step4() -> str:
    return INSTALL.split("Step 4 ", 1)[1].split("# ---------------------------------------------------------------- 5. models", 1)[0]


def test_step_4_installs_the_runtime_before_the_first_llama_wheel():
    s4 = _step4()
    check = s4.index("Get-TlwuMissingVcDlls")
    assert check < s4.index("requirements-llama-cuda.txt") and check < s4.index("requirements-llama-cpu.txt")
    assert "Install-TlwuVcRuntime" in s4 and "[dry-run]" in s4  # -DryRun prints the plan, downloads nothing


def test_install_stops_before_the_model_download_when_the_runtime_was_missing_and_the_engine_still_fails():
    gate = INSTALL.split("# ---------------------------------------------------------------- 5. models", 1)[0].rsplit("# #705", 1)[1]
    assert "$VcMissing.Count -gt 0" in gate and "-not $DryRun" in gate and "$Engine -ne 'hymt2'" in gate
    assert "Die " in gate  # exit 1: the wizard reports a failed install, and models are not downloaded
    assert INSTALL.index("Die \"缺少 Microsoft Visual C++") < INSTALL.index("Step 5 ")


def test_wizard_finish_page_shows_the_last_install_log_line_so_the_cause_is_visible():
    page = ISS.split("procedure CurPageChanged", 1)[1].split("\nend;", 1)[0]
    assert "InstallRC <> 0" in page and "LastLine" in page
    run = ISS.split("procedure RunInstall", 1)[1].split("\nend;", 1)[0]
    # the final log lines are read once more after install.done appears (the poll can be 5 s behind)
    assert run.index("LastStep(LogFile);   { #705") > run.index("while (not FileExists(DoneFile))")
