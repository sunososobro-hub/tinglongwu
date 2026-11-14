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


def test_selected_label_keeps_heavy_mark(tmp_path):
    assert "too heavy" in core.label_for_model("medium", tmp_path, ram_gb=16)
    assert "too heavy" not in core.label_for_model("medium", tmp_path)
    assert "too heavy" not in core.label_for_model("small", tmp_path, ram_gb=16)


def test_apply_cpu_mode_lowers_auto_model_and_sets_beam_threads(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tinglongwu import cli

    monkeypatch.delenv("TLWU_CPU_THREADS", raising=False)
    monkeypatch.setattr("tinglongwu.models.whisper_installed", lambda mdir=None: {"small", "medium"})
    a = SimpleNamespace(model="medium", quality="auto")
    st = cli.apply_cpu_mode(a, "cpu", tmp_path, explicit_model=False, cores=8, ram_gb=8)
    assert (a.model, a.cpu_beam, a.cpu_threads, st.size) == ("small", 1, 7, "small")


def test_apply_cpu_mode_keeps_explicit_model_and_env_threads(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tinglongwu import cli

    monkeypatch.setenv("TLWU_CPU_THREADS", "2")
    monkeypatch.setattr("tinglongwu.models.whisper_installed", lambda mdir=None: {"small", "medium"})
    a = SimpleNamespace(model="medium", quality="auto")
    cli.apply_cpu_mode(a, "cpu", tmp_path, explicit_model=True, cores=8, ram_gb=8)
    assert a.model == "medium" and a.cpu_threads == 2  # the env override reaches the recognizer


def test_apply_cpu_mode_is_a_noop_on_cuda(tmp_path):
    from types import SimpleNamespace
    from tinglongwu import cli

    a = SimpleNamespace(model="large-v3", quality="auto")
    assert cli.apply_cpu_mode(a, "cuda", tmp_path, explicit_model=False) is None
    assert a.model == "large-v3" and not hasattr(a, "cpu_beam")


def test_apply_cpu_mode_merge_raises_unset_min_words_only(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tinglongwu import cli

    monkeypatch.delenv("TLWU_CPU_THREADS", raising=False)
    unset = SimpleNamespace(model="small", quality="auto", min_words=0, min_words_explicit=False)
    cli.apply_cpu_mode(unset, "cpu", tmp_path, explicit_model=True, cores=4, ram_gb=8)  # merge=True
    assert unset.min_words == 3
    explicit = SimpleNamespace(model="small", quality="auto", min_words=0, min_words_explicit=True)
    cli.apply_cpu_mode(explicit, "cpu", tmp_path, explicit_model=True, cores=4, ram_gb=8)
    assert explicit.min_words == 0
    big = SimpleNamespace(model="small", quality="auto", min_words=0, min_words_explicit=False)
    cli.apply_cpu_mode(big, "cpu", tmp_path, explicit_model=True, cores=16, ram_gb=32)  # merge=False: unchanged
    assert big.min_words == 0
