"""#780 / #745 part 1: CPU-mode auto settings, "too heavy" model marks, "CPU mode" status line."""
import pytest

from tinglongwu.gui import core
from tinglongwu.hwprobe import CpuMode, cpu_mode_settings, too_heavy


@pytest.mark.parametrize("cores,ram,expected", [
    (4, 8, CpuMode("small", 1, 2, True)),
    (4, 16, CpuMode("small", 1, 2, True)),
    (4, 32, CpuMode("small", 1, 2, True)),
    (8, 8, CpuMode("small", 1, 4, True)),
    (8, 16, CpuMode("small", 2, 4, False)),
    (8, 32, CpuMode("small", 2, 4, False)),
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
    assert (a.model, a.cpu_beam, a.cpu_threads, st.size) == ("small", 1, 4, "small")


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


def test_cuda_fallback_gets_cpu_policy(monkeypatch):
    from tinglongwu.asr import cpu_fallback_settings

    monkeypatch.delenv("TLWU_CPU_THREADS", raising=False)
    assert cpu_fallback_settings(None, None, cores=8, ram_mib=8 * 1024) == (1, 4)  # not the old beam 5 / default threads
    assert cpu_fallback_settings(2, 3, cores=8, ram_mib=8 * 1024) == (2, 3)  # values from the CPU policy win
    monkeypatch.setenv("TLWU_CPU_THREADS", "2")
    assert cpu_fallback_settings(None, None, cores=8, ram_mib=32 * 1024)[1] == 2


def test_gui_ram_probe_uses_usable_cuda(monkeypatch):
    """CUDA devices exist but the libs are missing: resolve_device says cpu, so the heavy marks must apply."""
    from tinglongwu import device

    assert device.resolve_device("auto", count=lambda: 1, missing=lambda: ["cublas"], warn=lambda m: None) == "cpu"


def test_cpu_on_work_reports_mt_ms_and_keeps_slowwatch():
    from tinglongwu import cli

    seen, ms = [], []
    on_work = cli.cpu_on_work(lambda a, m, s: seen.append((a, m, s)), ms.append)
    on_work(0.5, 0.412, 3.0)
    assert seen == [(0.5, 0.412, 3.0)] and ms == [pytest.approx(412.0)]
    only_ms = cli.cpu_on_work(None, ms.append)
    only_ms(0.1, 0.2, 1.0)
    assert ms[-1] == pytest.approx(200.0)
    assert cli.cpu_on_work(None, None) is None  # no GUI, no SlowWatch (GPU or --file): unchanged
    watch = lambda a, m, s: None  # noqa: E731
    assert cli.cpu_on_work(watch, None) is watch


# #1198: Whisper and Hy-MT2 run at the same time in the live app. On an 8-core / 16-thread laptop CPU mode used to
# give Whisper 8 threads and llama.cpp 8 decode + 16 prompt threads: 24 busy threads on 16 CPUs.
@pytest.mark.parametrize("cores", [1, 2, 3, 4, 6, 8, 12, 16, 32])
def test_cpu_mode_threads_never_oversubscribe(cores):
    from tinglongwu.hwprobe import cpu_mode_threads

    t = cpu_mode_threads(cores)
    assert t.asr >= 1 and t.mt >= 1 and t.mt_batch == t.mt
    assert cores == 1 or t.asr + t.mt <= cores
    assert t.asr == cpu_mode_settings(cores, 16).threads


def test_apply_cpu_mode_hands_mt_threads_to_llama(tmp_path, monkeypatch):
    """Ryzen 7040 shape (16 logical CPUs): llama.cpp gets 8 decode / 8 prompt threads (was 8 / 16), Whisper 8."""
    from types import SimpleNamespace
    from tinglongwu import cli, threads

    monkeypatch.delenv("TLWU_CPU_THREADS", raising=False)
    monkeypatch.delenv("TLWU_BOTH", raising=False)
    monkeypatch.setattr(threads, "_role", "listen")
    monkeypatch.setattr(threads.os, "cpu_count", lambda: 16)
    monkeypatch.setattr("tinglongwu.models.whisper_installed", lambda mdir=None: {"small"})
    a = SimpleNamespace(model="small", quality="auto", min_words=0, min_words_explicit=True)
    assert (threads.current()["mt"], threads.current()["mt_batch"]) == (8, 16)  # llama.cpp defaults before
    cli.apply_cpu_mode(a, "cpu", tmp_path, explicit_model=True, cores=16, ram_gb=16)
    t = threads.current()
    assert (t["mt"], t["mt_batch"]) == (8, 8)
    assert a.cpu_threads + t["mt"] <= 16 and a.cpu_threads + t["mt_batch"] <= 16


def test_cpu_mode_mt_threads_cleared_on_gpu_and_ignored_in_listen_speak(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tinglongwu import cli, threads

    monkeypatch.delenv("TLWU_CPU_THREADS", raising=False)
    monkeypatch.setattr(threads, "_role", "listen")
    monkeypatch.setattr(threads.os, "cpu_count", lambda: 4)
    monkeypatch.setattr("tinglongwu.models.whisper_installed", lambda mdir=None: {"small"})
    a = SimpleNamespace(model="small", quality="auto", min_words=0, min_words_explicit=True)
    cli.apply_cpu_mode(a, "cpu", tmp_path, explicit_model=True, cores=4, ram_gb=16)
    assert (threads.current()["mt"], threads.current()["mt_batch"]) == (2, 2)
    monkeypatch.setenv("TLWU_BOTH", "1")  # listen+speak keeps the #250 split
    assert threads.current()["mt"] == threads.split(4)["listen"]["mt"]
    monkeypatch.delenv("TLWU_BOTH")
    cli.apply_cpu_mode(SimpleNamespace(model="large-v3", quality="auto"), "cuda", tmp_path, explicit_model=False)
    assert (threads.current()["mt"], threads.current()["mt_batch"]) == (2, 4)  # llama.cpp defaults again


def test_cuda_to_cpu_fallback_gets_cpu_mode_mt_threads(monkeypatch):
    from tinglongwu import cli, threads

    monkeypatch.delenv("TLWU_BOTH", raising=False)
    monkeypatch.setattr(threads, "_role", "listen")
    monkeypatch.setattr(threads.os, "cpu_count", lambda: 4)
    assert not cli.cpu_fallback_mt("cuda", "cuda", cores=4) and not cli.cpu_fallback_mt("cpu", "cpu", cores=4)
    assert (threads.current()["mt"], threads.current()["mt_batch"]) == (2, 4)
    assert cli.cpu_fallback_mt("cuda", "cpu", cores=4)
    assert (threads.current()["mt"], threads.current()["mt_batch"]) == (2, 2)


def test_eval_defaults_run_after_cpu_mode_run_clears_mt_threads(monkeypatch):
    from types import SimpleNamespace
    from tinglongwu import threads
    from tlwu_eval.__main__ import cpu_mode_asr

    monkeypatch.delenv("TLWU_BOTH", raising=False)
    monkeypatch.setattr(threads, "_role", "listen")
    monkeypatch.setattr(threads.os, "cpu_count", lambda: 4)
    cpu_mode_asr(SimpleNamespace(cpu_mode=True, model=None), "cpu", "small", cores=4, ram_gb=16)
    assert threads.current()["mt_batch"] == 2
    cpu_mode_asr(SimpleNamespace(cpu_mode=False, model=None), "cpu", "small", cores=4, ram_gb=16)
    assert threads.current()["mt_batch"] == 4  # llama.cpp default again for the baseline run
