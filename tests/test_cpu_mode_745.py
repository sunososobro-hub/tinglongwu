"""#780 / #745 part 1: CPU-mode auto settings, "too heavy" model marks, "CPU mode" status line."""
import pytest

from tinglongwu.gui import core
from tinglongwu.hwprobe import CpuMode, cpu_mode_settings, too_heavy


@pytest.mark.parametrize("cores,ram,expected", [
    (4, 8, CpuMode("small", 1, 3, True)),
    (4, 16, CpuMode("small", 1, 3, True)),
    (4, 32, CpuMode("small", 1, 3, True)),
    (8, 8, CpuMode("small", 1, 7, True)),
    (8, 16, CpuMode("small", 2, 7, False)),
    (8, 32, CpuMode("small", 2, 7, False)),
    (16, 8, CpuMode("small", 1, 8, True)),
    (16, 16, CpuMode("small", 2, 8, False)),
    (16, 32, CpuMode("small", 2, 8, False)),
])
def test_cpu_mode_settings(cores, ram, expected):
    assert cpu_mode_settings(cores, ram) == expected


def test_low_ram_or_two_cores_use_base():
    assert cpu_mode_settings(8, 4).size == "base"
    assert cpu_mode_settings(2, 32) == CpuMode("base", 1, 1, True)
    assert cpu_mode_settings(1, 32).threads == 1


def test_too_heavy_below_24_gb():
    assert too_heavy("medium", 16) and too_heavy("large-v3", 23.9)
    assert not too_heavy("medium", 24) and not too_heavy("small", 8) and not too_heavy("medium", None)


def test_model_picker_marks_heavy_models(tmp_path):
    marked = core.model_labels(tmp_path, ram_gb=16)
    heavy = [l for l in marked if "too heavy" in l]
    assert sorted(core.model_from_label(l) for l in heavy) == ["large-v3", "medium"]
    assert not any("too heavy" in l for l in core.model_labels(tmp_path, ram_gb=32))
    assert not any("too heavy" in l for l in core.model_labels(tmp_path))


def test_cpu_ram_only_without_cuda():
    assert core.cpu_ram_gb(True, ram=lambda: 16384) is None
    assert core.cpu_ram_gb(False, ram=lambda: 16384) == 16
    assert core.cpu_ram_gb(False, ram=lambda: None) is None


@pytest.mark.parametrize("lang,word", [("en", "CPU mode"), ("ru", "CPU"), ("zh-TW", "CPU 模式")])
def test_cpu_mode_line(monkeypatch, lang, word):
    monkeypatch.setenv("TLWU_UI_LANG", lang)
    line = core.cpu_mode_line("cpu", 412.4)
    assert word in line and "412" in line
    assert core.cpu_mode_line("cpu") and "412" not in core.cpu_mode_line("cpu")
    assert core.cpu_mode_line("cuda", 5) is None
