"""#63 part 1: hardware probe + tier recommendation (behaviour, with fake nvidia-smi / RAM readers)."""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

from tinglongwu import hwprobe
from tinglongwu.hwprobe import HwInfo, gpu_info, probe, recommend


def fake_run(stdout="", exc=None):
    def run(cmd, **kw):
        assert cmd[0] == "nvidia-smi"
        if exc:
            raise exc
        return SimpleNamespace(stdout=stdout)
    return run


def test_gpu_info_parses_name_with_commas_and_total():
    assert gpu_info(fake_run("NVIDIA GeForce RTX 3050, 8192\n")) == ("NVIDIA GeForce RTX 3050", 8192)
    assert gpu_info(fake_run("Quadro, Ada, 4096\nOther, 2048\n")) == ("Quadro, Ada", 4096)


def test_gpu_info_none_without_driver_or_garbage():
    assert gpu_info(fake_run(exc=FileNotFoundError("nvidia-smi"))) == (None, None)
    assert gpu_info(fake_run("No devices were found\n")) == (None, None)
    assert gpu_info(fake_run("")) == (None, None)


def test_ram_mib_is_real_and_plausible():
    mib = hwprobe.ram_mib()
    assert mib is None or 256 < mib < 64 * 1024 * 1024


def test_probe_combines_readers():
    hw = probe(gpu=lambda: ("RTX", 6144), ram=lambda: 16000, cores=lambda: None)
    assert hw == HwInfo("RTX", 6144, 16000, 1)


def test_8gb_gpu_gets_gpu_tier_with_hymt_and_turbo():
    t = recommend(HwInfo("NVIDIA GeForce RTX 3050", 8192, 32768, 20))
    assert (t.name, t.device, t.asr, t.mt) == ("gpu", "cuda", "large-v3-turbo", "hymt2")
    assert t.excluded == {}  # 8 GB holds turbo + Hy-MT2 (1600 + 1500 <= 8192 - 512)
    t = recommend(HwInfo("RTX 2060", 6144, 16384, 12))
    assert (t.name, t.mt) == ("gpu", "hymt2")
    assert t.excluded == {}  # 1600 + 1500 <= 6144 - 512


def test_4gb_gpu_is_still_gpu_tier_but_excludes_large_models():
    t = recommend(HwInfo("GTX 1650", 4096, 8192, 8))
    assert (t.name, t.asr, t.mt) == ("gpu", "large-v3-turbo", "hymt2")
    for m in ("whisper large-v3 (GPU)",):
        assert m in t.excluded, m
    assert "hymt2 (GPU)" not in t.excluded


def test_small_gpu_and_no_gpu_fall_back_to_cpu_opus():
    t = recommend(HwInfo("GT 1030", 2048, 8192, 4))
    assert (t.name, t.device, t.asr, t.mt) == ("cpu", "cpu", "small", "opus")
    assert "2048 MiB" in t.reason and "hymt2 (GPU)" in t.excluded
    t = recommend(HwInfo(None, None, 16384, 8))
    assert (t.name, t.mt) == ("cpu", "opus") and "no NVIDIA GPU" in t.reason
    assert t.excluded == {}


def test_low_ram_cpu_excludes_hymt_on_cpu():
    assert "hymt2 (CPU)" in recommend(HwInfo(None, None, 3000, 4)).excluded
    assert "hymt2 (CPU)" not in recommend(HwInfo(None, None, None, 4)).excluded


def test_cli_probe_prints_recommendation_and_exits(tmp_path):
    # real CLI process; an nvidia-smi stub on PATH stands in for the driver
    stub = tmp_path / "nvidia-smi"
    stub.write_text("#!/bin/sh\necho 'NVIDIA GeForce RTX 2060, 6144'\n")
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    if sys.platform == "win32":
        return  # shell stub is POSIX-only; the parsing is covered above
    r = subprocess.run([sys.executable, "-m", "tinglongwu", "--probe"], capture_output=True, text=True,
                       env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "RTX 2060" in r.stdout and "GPU tier" in r.stdout and "hymt2" in r.stdout
    assert "hymt2 (GPU)" not in r.stdout.split("not used:")[-1]
