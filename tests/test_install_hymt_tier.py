"""#106: the Windows installer picks Hy-MT2 for the GPU tier (>= 4 GB total VRAM, hwprobe.GPU_MIN_VRAM_MIB);
below that (or without a GPU) Hy-MT2 runs on the CPU (#301). Runs `install.ps1 -DryRun` with a stubbed nvidia-smi and python.

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


def dry_run(tmp_path: Path, total_mb: int | None, *extra: str) -> str:
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
    r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(ROOT / "install.ps1"), "-DryRun", *extra],
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
    assert not any("--qwen" in c for c in calls), calls  # the old LLM download flag is gone (#308)
    assert any("--whisper large-v3-turbo" in c for c in calls), calls
    assert "requirements-llama-cuda.txt" in out  # llama-cpp-python CUDA wheel: Hy-MT2 runs on llama.cpp
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
    assert "requirements-llama-cuda.txt" not in out and "requirements-llama-cpu.txt" in out
    assert "Translation engine / 翻譯引擎: hymt2" in out
    assert "OPUS engine" not in out


def test_lang_ja_prints_english_only(tmp_path):
    """#1002: -Lang ja (any non-Chinese wizard language) -> English-only labels, no mixed "中文 / English"."""
    out = dry_run(tmp_path, 8192, "-Lang", "ja")
    lines = [ln.strip() for ln in out.splitlines()]
    assert any(ln.startswith("Translation engine: hymt2") for ln in lines)
    assert "Installed:" in lines and any(ln.startswith("Folder: ") for ln in lines)
    assert any(ln.startswith("Self-test: ") for ln in lines)
    assert any(ln.startswith("[1/8] ") and not any("\u4e00" <= c <= "\u9fff" for c in ln) for ln in lines)
    assert not any(" / 翻譯引擎" in ln or " / 已安裝" in ln or " / 資料夾" in ln for ln in lines)


def test_lang_zh_tw_prints_chinese_only(tmp_path):
    """#1002: -Lang zh-TW -> Chinese-only labels."""
    out = dry_run(tmp_path, 8192, "-Lang", "zh-TW")
    lines = [ln.strip() for ln in out.splitlines()]
    assert any(ln.startswith("翻譯引擎: hymt2") for ln in lines)
    assert "已安裝:" in lines and any(ln.startswith("資料夾: ") for ln in lines)
    assert any(ln.startswith("自我測試: ") for ln in lines)
    assert not any(ln.startswith(("Translation engine", "Installed", "Folder")) for ln in lines)


def test_no_lang_keeps_the_old_bilingual_labels(tmp_path):
    """#1002: without -Lang every summary line keeps its exact old text (install.bat / CI callers)."""
    out = dry_run(tmp_path, 8192)
    for label in ("Folder / 資料夾: ", "Installed / 已安裝:", "Translation engine / 翻譯引擎: hymt2", "Self-test / 自我測試:"):
        assert label in out, label
