"""#1772: tlwu-setup --check-gpu report, Vulkan iGPU state for the GUI, and the status-line text builders."""
from pathlib import Path

from tinglongwu.gui import gpu_offer
from tinglongwu_setup import cli, gpucheck
from tlwu_app import tiers

APP = "0.5.2"
SUMMARY = """GPUs:
GPU0:
\tdeviceType         = PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU
\tdeviceName         = AMD Radeon(TM) 780M Graphics
GPU1:
\tdeviceName         = llvmpipe (LLVM 15.0.7, 256 bits)
"""


def make_pack(root: Path, version: str | None, whisper: bool = False) -> Path:
    pack = root / "vulkan-pack"
    pack.mkdir(parents=True)
    (pack / "libllama.so").write_bytes(b"x")
    if version:
        (pack / "version.txt").write_text(version + "\n")
    if whisper:
        (pack / "whisper").mkdir()
        (pack / "whisper" / "libtlwu-whisper.so").write_bytes(b"x")
    return pack


def run_report(tmp_path, devices, platform="linux"):
    lines = []
    rc = gpucheck.run(tmp_path, tmp_path / "models", APP, env={}, platform=platform, devices=devices, out=lines.append)
    assert rc == 0
    return "\n".join(lines)


def test_parse_devices_drops_software_rasterizer():
    assert gpucheck.parse_devices(SUMMARY) == ["AMD Radeon(TM) 780M Graphics"]


def test_four_distinct_outputs(tmp_path):
    none = run_report(tmp_path / "a", [])
    assert "Vulkan devices: none" in none and "ASR: CPU (no Vulkan device found)" in none

    nopack = run_report(tmp_path / "b", ["AMD 780M"])
    assert "Vulkan devices: AMD 780M" in nopack and "not installed" in nopack and "ASR: CPU" in nopack

    make_pack(tmp_path / "c", "0.4.0", whisper=True)
    stale = run_report(tmp_path / "c", ["AMD 780M"])
    assert "STALE, version 0.4.0 vs app 0.5.2" in stale and "MT: CPU" in stale

    make_pack(tmp_path / "d", APP, whisper=True)
    (tmp_path / "d" / "models" / "whisper-cpp").mkdir(parents=True)
    (tmp_path / "d" / "models" / "whisper-cpp" / "ggml-small.bin").write_bytes(b"x")
    full = run_report(tmp_path / "d", ["AMD 780M"])
    assert "ASR: VULKAN" in full and "MT: VULKAN" in full and "ggml-small.bin" in full
    assert len({none, nopack, stale, full}) == 4


def test_pack_without_model_keeps_mt_on_vulkan_but_asr_on_cpu(tmp_path):
    make_pack(tmp_path, APP, whisper=True)
    text = run_report(tmp_path, ["Intel Arc"])
    assert "MT: VULKAN" in text and "ASR: CPU (no whisper.cpp model" in text and "missing" in text


def test_check_gpu_cli_runs_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TLWU_OFFLINE", "1")
    monkeypatch.setattr(gpucheck, "probe_devices", lambda: [])
    assert cli.main(["--check-gpu", "--models-dir", str(tmp_path), "--app-version", APP]) == 0
    assert "Vulkan devices: none" in capsys.readouterr().out


def test_detect_returns_vulkan_state_without_nvidia(tmp_path):
    state = gpu_offer.detect(gpu_info=lambda: (None, None), pack_dir=lambda: None, platform="linux",
                             vulkan=lambda: ["AMD Radeon 780M"])
    assert state == gpu_offer.VULKAN
    assert gpu_offer.detect(gpu_info=lambda: (None, None), pack_dir=lambda: None, platform="linux",
                            vulkan=lambda: []) == gpu_offer.UNAVAILABLE
    assert gpu_offer.detect(gpu_info=lambda: (None, None), pack_dir=lambda: None, platform="darwin",
                            vulkan=lambda: ["x"]) == gpu_offer.UNAVAILABLE


def test_vulkan_state_is_not_a_cuda_pack_offer():
    assert not gpu_offer.should_offer(gpu_offer.VULKAN, False, False, False)


def test_gui_text_names_igpu_and_pack_state_not_nvidia():
    ready = gpu_offer.row_text(gpu_offer.VULKAN, None, ["AMD Radeon 780M"], pack_ok=lambda: True)
    assert "AMD Radeon 780M" in ready and "NVIDIA" not in ready and "installed" in ready
    missing = gpu_offer.row_text(gpu_offer.VULKAN, None, ["AMD Radeon 780M"], pack_ok=lambda: False)
    assert "AMD Radeon 780M" in missing and "not installed" in missing and missing != ready
    setup = tiers.detection_text(None, vulkan=["AMD Radeon 780M"])
    assert "AMD Radeon 780M" in setup and "Vulkan" in setup
    assert tiers.detection_text(None).startswith("沒有偵測到 NVIDIA")


def test_vulkan_devices_parses_vulkaninfo():
    class P:
        stdout = SUMMARY

    assert gpu_offer.vulkan_devices(run=lambda *a, **k: P(), which=lambda n: "/usr/bin/vulkaninfo") == [
        "AMD Radeon(TM) 780M Graphics"]
    assert gpu_offer.vulkan_devices(which=lambda n: None) == []


def test_missing_vulkaninfo_is_not_reported_as_no_device(tmp_path, monkeypatch):
    """#1782: no `vulkaninfo` on PATH names the missing tool instead of claiming there is no Vulkan device."""
    lines = []
    monkeypatch.setattr(gpucheck, "probe_devices", lambda *a, **k: [])
    gpucheck.run(tmp_path, tmp_path / "models", APP, env={}, platform="linux", out=lines.append, which=lambda n: None)
    text = "\n".join(lines)
    assert "vulkaninfo" in text and "probe tool missing" in text
    assert "no Vulkan device found" not in text


def test_first_run_text_shows_vulkan_name_from_probe():
    class P:
        stdout = SUMMARY

    names = tiers.vulkan_names(run=lambda *a, **k: P(), which=lambda n: "/usr/bin/vulkaninfo")
    assert names == ["AMD Radeon(TM) 780M Graphics"]
    assert "AMD Radeon(TM) 780M Graphics" in tiers.detection_text(None, vulkan=names)
    assert tiers.vulkan_names(which=lambda n: None) == []
