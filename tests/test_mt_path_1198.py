"""#1198 (3): the CPU-mode status line names where Hy-MT2 really runs (CPU + threads, or GPU via Vulkan) next to
the per-line MT ms, so a user on an AMD / Intel laptop can tell the slow CPU path from the Vulkan one."""
import sys
import types

import pytest

from tinglongwu import cli, engines, llamacheck, mt_backend
from tinglongwu import threads as thread_budget
from tinglongwu.gui import core
from tinglongwu.hymt import HyMtTranslator, JaRouter, active_path, hymt_path


def fake_llama_module(llama_cls):
    m = types.ModuleType("llama_cpp")
    m.__file__ = "nowhere/llama_cpp/__init__.py"
    m.__version__ = "0.3.36"
    m.llama_print_system_info = lambda: "AVX = 1 | AVX2 = 1 |"
    m.llama_supports_gpu_offload = lambda: True
    m.Llama = llama_cls
    return m


@pytest.fixture
def no_cpu_check(monkeypatch):
    monkeypatch.setattr(llamacheck, "check_build", lambda mod, cpu=None: None)
    monkeypatch.delenv("TLWU_DEVICE", raising=False)


def router(tmp_path, loader):
    f = hymt_path(tmp_path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"gguf")
    return JaRouter(None, tmp_path, "tw", "cpu", all_pairs=True,
                    factory=lambda m, z, d: HyMtTranslator(m, z, d, loader=loader))


def test_cpu_load_reports_cpu_and_its_threads(monkeypatch, tmp_path, no_cpu_check):
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_module(lambda **kw: ("cpu-model", kw)))
    monkeypatch.setattr(mt_backend, "vulkan_available", lambda: False)
    tr = router(tmp_path, engines.load_llama)
    assert active_path(tr) is None  # not loaded yet: no claim
    tr.preload("en", "zh")
    assert active_path(tr) == ("cpu", thread_budget.current()["mt"])
    assert active_path(tr)[1] >= 1


def test_vulkan_load_reports_vulkan(monkeypatch, tmp_path, no_cpu_check):
    monkeypatch.setattr("tinglongwu.gui.core._TECH", {"flag": False, "setting": True})  # #1807: technical view names the path
    pack = tmp_path / "vulkan-pack"
    pack.mkdir()
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_module(lambda **kw: ("gpu-model", kw)))
    monkeypatch.setattr(mt_backend, "vulkan_available", lambda: True)
    monkeypatch.setattr(mt_backend, "vulkan_pack_dir", lambda *a, **k: pack)
    monkeypatch.setenv("LLAMA_CPP_LIB_PATH", str(pack))
    tr = router(tmp_path, engines.load_llama)
    tr.preload("en", "zh")
    assert active_path(tr)[0] == "vulkan"
    assert "Vulkan" in core.cpu_mode_line("cpu", 900, path=active_path(tr))


def test_vulkan_init_failure_reports_cpu_not_vulkan(monkeypatch, tmp_path, no_cpu_check, capsys):
    """The status line must follow the fallback: a Vulkan driver that fails to init means MT runs on the CPU."""
    pack = tmp_path / "vulkan-pack"
    pack.mkdir()

    def llama(**kw):
        if kw["n_gpu_layers"] != 0:
            raise RuntimeError("vk::createInstance: ErrorIncompatibleDriver")
        return ("cpu-model", kw)

    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_module(llama))
    monkeypatch.setattr(mt_backend, "vulkan_available", lambda: True)
    monkeypatch.setattr(mt_backend, "vulkan_pack_dir", lambda *a, **k: pack)
    monkeypatch.setenv("LLAMA_CPP_LIB_PATH", str(pack))
    tr = router(tmp_path, engines.load_llama)
    tr.preload("en", "zh")
    assert active_path(tr)[0] == "cpu"
    assert "Vulkan init failed" in capsys.readouterr().err


def test_each_shared_model_keeps_its_own_path(tmp_path):
    """A later load of another GGUF (e.g. topic notes) moves the module globals but not this model's path."""
    def loader(path, gpu):
        mt_backend.last_backend, mt_backend.last_threads = ("vulkan", 0) if "a" in path.name else ("cpu", 3)
        return object()

    a, b = tmp_path / "a.gguf", tmp_path / "b.gguf"
    la, lb = engines.shared_llama(a, False, loader), engines.shared_llama(b, False, loader)
    assert mt_backend.path_of(la) == ("vulkan", 0) and mt_backend.path_of(lb) == ("cpu", 3)
    assert mt_backend.path_of(object()) is None


def test_other_engines_have_no_path():
    assert active_path(object()) is None
    assert cli.active_mt_path(None) is None


@pytest.mark.parametrize("lang,cpu,gpu", [
    ("en", "Hy-MT2 on CPU, 4 threads", "Hy-MT2 on GPU (Vulkan)"),
    ("zh-TW", "Hy-MT2 在 CPU 上跑，4 執行緒", "Hy-MT2 在顯示卡上跑（Vulkan）"),
    ("ru", "Hy-MT2 на CPU, потоков: 4", "Hy-MT2 на GPU (Vulkan)"),
    ("ja", "Hy-MT2 は CPU、4 スレッド", "Hy-MT2 は GPU（Vulkan）"),
])
def test_status_line_names_the_path(monkeypatch, lang, cpu, gpu):
    monkeypatch.setattr("tinglongwu.gui.core._TECH", {"flag": False, "setting": True})  # #1807: technical view names the path
    monkeypatch.setenv("TLWU_UI_LANG", lang)
    line = core.cpu_mode_line("cpu", 2920.4, path=("cpu", 4))
    assert "2920" in line and cpu in line
    assert gpu in core.cpu_mode_line("cpu", 812, path=("vulkan", 0))
    assert core.cpu_mode_line("cpu", 812) == core.cpu_mode_line("cpu", 812, path=None)  # no path: #780 line as is


def test_cpu_on_work_passes_the_path_with_the_ms():
    got = []
    on_work = cli.cpu_on_work(None, lambda ms, path: got.append((ms, path)), lambda: ("vulkan", 0))
    on_work(0.4, 0.75, 2.0)
    assert got == [(pytest.approx(750.0), ("vulkan", 0))]


def test_engine_log_line_says_where_the_model_loaded(monkeypatch, tmp_path, no_cpu_check):
    """`translation engine = hymt2 on <backend>` follows the real load, not the Whisper device."""
    pack = tmp_path / "vulkan-pack"
    pack.mkdir()
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_module(lambda **kw: ("gpu-model", kw)))
    monkeypatch.setattr(mt_backend, "vulkan_available", lambda: True)
    monkeypatch.setattr(mt_backend, "vulkan_pack_dir", lambda *a, **k: pack)
    monkeypatch.setenv("LLAMA_CPP_LIB_PATH", str(pack))
    f = hymt_path(tmp_path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"gguf")
    said = []
    tr = cli.make_translator("auto", tmp_path, "tw", "cpu", say=lambda m, **k: said.append(m),
                             choose=lambda e, d, m: ("hymt2", "test"))
    assert active_path(tr)[0] == "vulkan"
    assert any("translation engine = hymt2 on vulkan" in s for s in said), said


def test_cuda_build_that_fell_back_to_cpu_is_reported_and_loaded_as_cpu(monkeypatch, tmp_path, no_cpu_check):
    """import_llama() swapped in the bundled CPU build (CUDA DLLs missing): the model is a CPU model, not CUDA."""
    made = []
    mod = fake_llama_module(lambda **kw: made.append(kw) or ("cpu-model", kw))

    def import_cpu_build():
        monkeypatch.setattr(llamacheck, "cpu_fallback", "OSError: cublas64_12.dll not found")
        return mod

    monkeypatch.setitem(sys.modules, "llama_cpp", mod)
    monkeypatch.setattr(llamacheck, "import_llama", import_cpu_build)
    monkeypatch.setattr(mt_backend, "vulkan_available", lambda: False)
    llm = engines.shared_llama(tmp_path / "x.gguf", True, engines.load_llama)
    assert mt_backend.path_of(llm)[0] == "cpu"
    assert made[-1]["n_gpu_layers"] == 0 and made[-1]["flash_attn"] is False
