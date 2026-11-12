"""#106: the Windows installer picks Hy-MT2 for the GPU tier (>= 4 GB total VRAM, hwprobe.GPU_MIN_VRAM_MIB) instead of
choosing Qwen by VRAM; below that (or without a GPU) Hy-MT2 runs on the CPU (#301). Runs `install.ps1 -DryRun` with a stubbed nvidia-smi and python.

Needs `pwsh` (preinstalled on the GitHub ubuntu runners); skipped where it is missing."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tinglongwu import hwprobe

ROOT = Path(__file__).resolve().parent.parent
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="pwsh not installed")


def _exe(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def dry_run(tmp_path: Path, total_mb: int | None) -> str:
    """stdout of install.ps1 -DryRun on a PC whose NVIDIA GPU has `total_mb` MiB (None = no nvidia-smi)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # step 1 asks `python` for sys.executable; answer with this interpreter so the installer finds "Python"
    _exe(bin_dir / "python", f'echo "{sys.executable}"')
    if total_mb is not None:
        _exe(bin_dir / "nvidia-smi", f'echo "Test GPU, {total_mb}, {total_mb - 500}"')
    env = {k: v for k, v in os.environ.items() if k not in ("TLWU_SKIP_MODELS", "TLWU_SKIP_SHORTCUT")}
    env["TLWU_TEST_RAM_MB"] = "2048"  # low RAM: Hy-MT2 is still installed, with a warning (#301)
    env.update(PATH=f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin", TLWU_SKIP_SHORTCUT="1",
               POWERSHELL_TELEMETRY_OPTOUT="1", POWERSHELL_UPDATECHECK="Off")
    r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(ROOT / "install.ps1"), "-DryRun"],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, env=env, cwd=str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def _setup_calls(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if "[dry-run]" in ln and "tlwu-setup" in ln]


@pytest.mark.parametrize("total", [hwprobe.GPU_MIN_VRAM_MIB, 6144, 8192, 24576])
def test_gpu_tier_downloads_hymt_not_qwen(tmp_path, total):
    out = dry_run(tmp_path, total)
    calls = _setup_calls(out)
    assert any(c.rstrip().endswith("--hymt") for c in calls), calls
    assert not any("--qwen" in c for c in calls), calls  # Qwen is opt-in only (tlwu-setup --qwen)
    assert any("--whisper large-v3-turbo" in c for c in calls), calls
    assert "requirements-qwen.txt" in out  # llama-cpp-python CUDA wheel: Hy-MT2 runs on llama.cpp
    assert "~3 GB" in out and "~10 GB" not in out
    assert "Translation engine / 翻譯引擎: hymt2" in out


@pytest.mark.parametrize("total", [hwprobe.GPU_MIN_VRAM_MIB - 1, 2048, None])
def test_below_threshold_or_no_gpu_runs_hymt_on_the_cpu(tmp_path, total):
    """#301: Hy-MT2 is the only default engine (#276); below 4 GB VRAM or without a GPU it runs on the CPU."""
    out = dry_run(tmp_path, total)
    calls = _setup_calls(out)
    assert any(c.rstrip().endswith("--hymt") for c in calls), calls
    assert not any("--qwen" in c for c in calls), calls
    assert any("--whisper small" in c for c in calls), calls
    assert "requirements-qwen.txt" not in out and "requirements-llama-cpu.txt" in out
    assert "Translation engine / 翻譯引擎: hymt2" in out
    assert "OPUS engine" not in out
