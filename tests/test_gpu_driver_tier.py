"""#95: a GPU whose NVIDIA driver is too old for CUDA 12 (e.g. GTX 1050 + driver 457.49) must get the CPU tier and an
update-driver hint at install / first-run setup time, not a surprise "switched to CPU" at run time.

Python: tlwu_app.tiers (first-run setup of tlwu.exe), fed fake nvidia-smi output. PowerShell: Get-TlwuGpu from
scripts/install_helpers.ps1 (install.ps1 step 2); needs `pwsh` (preinstalled on the GitHub ubuntu runners)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tlwu_app import tiers

ROOT = Path(__file__).resolve().parent.parent
PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh not installed")

OLD = "NVIDIA GeForce GTX 1050, 4096, 3900, 457.49\n"  # the owner's laptop in #89
NEW = "NVIDIA GeForce RTX 3050, 8192, 7478, 560.94\n"


def _fake_smi(monkeypatch, text):
    calls = []

    class R:
        stdout = text

    def run(cmd, **k):
        calls.append(cmd)
        return R()

    monkeypatch.setattr(tiers, "_nvidia_smi", lambda: "nvidia-smi")
    return run, calls


def test_parse_smi_reads_the_driver_version():
    assert tiers.parse_smi(OLD) == tiers.Gpu("NVIDIA GeForce GTX 1050", 4096, 3900, "457.49")
    assert tiers.parse_smi("RTX, 6144, 6000\n").driver == ""  # old 3-field query still parses
    assert tiers.parse_smi("RTX, 6144, 6000, [N/A]\n").driver == ""


def test_old_driver_gets_the_cpu_tier(monkeypatch):
    run, calls = _fake_smi(monkeypatch, OLD)
    raw = tiers.probe_gpu(run)
    assert any("driver_version" in a for a in calls[0][1:])  # the query asks nvidia-smi for the driver
    assert raw.driver == "457.49" and tiers.driver_too_old(raw, "win32")
    assert tiers.detect_gpu(run, platform="win32") is None
    assert tiers.choose_tier(tiers.detect_gpu(run, platform="win32")) is tiers.T_CPU
    assert tiers.T_CPU.whisper == "small" and tiers.T_CPU.mt == ("--hymt",)  # Hy-MT2 on the CPU (#301), no Qwen (owner #89: small, not base)


def test_new_driver_keeps_the_gpu_tier(monkeypatch):
    run, _ = _fake_smi(monkeypatch, NEW)
    gpu = tiers.detect_gpu(run, platform="win32")
    assert gpu is not None and gpu.driver == "560.94"
    assert tiers.choose_tier(gpu) is tiers.T_GPU
    # exactly the minimum is fine; unknown driver is not treated as too old (nothing to tell the user)
    assert not tiers.driver_too_old(tiers.Gpu("x", 4096, 4000, "528.33"), "win32")
    assert tiers.driver_too_old(tiers.Gpu("x", 4096, 4000, "528.24"), "win32")
    assert not tiers.driver_too_old(tiers.Gpu("x", 4096, 4000, ""), "win32")
    assert not tiers.driver_too_old(None)


def test_setup_window_text_tells_how_to_fix(monkeypatch):
    run, _ = _fake_smi(monkeypatch, OLD)
    txt = tiers.detection_text(tiers.probe_gpu(run), platform="win32")
    assert "457.49" in txt and "528.33" in txt and "https://www.nvidia.com/Download/index.aspx" in txt
    assert "CPU" in txt and "GTX 1050" in txt
    assert "RTX 3050" in tiers.detection_text(tiers.parse_smi(NEW), platform="win32")
    assert "沒有偵測到" in tiers.detection_text(None)
    assert tiers.detection_text(tiers.parse_smi(OLD), force_cpu=True) == "已指定 CPU 模式。"


def _gpu_ps(smi: str) -> dict | None:
    script = (". ./scripts/install_helpers.ps1; $g = Get-TlwuGpu $env:TLWU_TEST_SMI; "
              "if ($g) { $g | ConvertTo-Json -Compress } else { 'null' }")
    env = {**os.environ, "TLWU_TEST_SMI": smi, "POWERSHELL_TELEMETRY_OPTOUT": "1", "POWERSHELL_UPDATECHECK": "Off"}
    r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                       encoding="utf-8", timeout=120, env=env, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@needs_pwsh
def test_installer_flags_old_driver():
    g = _gpu_ps(OLD)
    assert g == {"Name": "NVIDIA GeForce GTX 1050", "TotalMB": 4096, "FreeMB": 3900, "Driver": "457.49",
                 "DriverOld": True}
    assert _gpu_ps(NEW)["DriverOld"] is False
    assert _gpu_ps("x, 4096, 4000, 528.33")["DriverOld"] is False
    assert _gpu_ps("x, 4096, 4000, 528.24")["DriverOld"] is True
    assert _gpu_ps("x, 4096, 4000, [N/A]") == {"Name": "x", "TotalMB": 4096, "FreeMB": 4000, "Driver": "",
                                                "DriverOld": False}
    assert _gpu_ps("") is None and _gpu_ps("garbage") is None and _gpu_ps("a, b, c, 457.49") is None


@needs_pwsh
def test_installer_and_runtime_use_the_same_threshold():
    from tinglongwu.device import DRIVER_URL, MIN_DRIVER

    script = ". ./scripts/install_helpers.ps1; \"$TlwuMinDriver|$TlwuDriverUrl\""
    r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                       timeout=120, cwd=str(ROOT))
    assert r.stdout.strip() == ".".join(map(str, MIN_DRIVER["win32"])) + "|" + DRIVER_URL
