"""#746: MT backend order CUDA > Vulkan > CPU, forced TLWU_DEVICE=vulkan, CPU fallback on Vulkan init failure."""
from __future__ import annotations

import pytest

from tinglongwu import mt_backend as mb
from tinglongwu.selftest import Result


@pytest.mark.parametrize("cuda,vulkan,want", [(True, True, "cuda"), (False, True, "vulkan"),
                                              (True, False, "cuda"), (False, False, "cpu")])
def test_probe_order(cuda, vulkan, want):
    assert mb.choose_backend(cuda=cuda, vulkan=vulkan, env={}) == want


def test_forced_vulkan_wins_over_cuda():
    assert mb.choose_backend(cuda=True, vulkan=True, env={"TLWU_DEVICE": "vulkan"}) == "vulkan"


def test_forced_vulkan_without_vulkan_is_next_best_not_error():
    assert mb.choose_backend(cuda=False, vulkan=False, env={"TLWU_DEVICE": "vulkan"}) == "cpu"


def test_vulkan_init_failure_falls_back_to_cpu(capsys):
    calls = []

    def loader(path, backend):
        calls.append(backend)
        if backend == "vulkan":
            raise RuntimeError("vkCreateInstance failed")
        return "model"

    model, used = mb.load_with_fallback("m.gguf", "vulkan", loader, warn=lambda m: print(m))
    assert (model, used, calls) == ("model", "cpu", ["vulkan", "cpu"])
    assert capsys.readouterr().out.count("Vulkan init failed") == 1


def test_cpu_error_is_not_swallowed():
    def loader(path, backend):
        raise RuntimeError("bad gguf")

    with pytest.raises(RuntimeError):
        mb.load_with_fallback("m.gguf", "cpu", loader)


def test_vulkan_available_needs_pack_and_loader(tmp_path):
    assert mb.vulkan_pack_dir(tmp_path) is None
    (tmp_path / "llama.dll").write_bytes(b"x")
    from tinglongwu import __version__

    (tmp_path / mb.VERSION_FILE).write_text(__version__, "utf-8")
    assert mb.vulkan_pack_dir(tmp_path) == tmp_path.resolve()
    assert mb.vulkan_available(pack=lambda: tmp_path, present=lambda: True)
    assert not mb.vulkan_available(pack=lambda: None, present=lambda: True)
    assert not mb.vulkan_available(pack=lambda: tmp_path, present=lambda: False)


def test_selftest_reports_mt_backend():
    r = Result(ok=True, mt_backend="vulkan")
    assert "mt: vulkan" in r.text()


def _fake_pack(tmp_path):
    (tmp_path / "llama.dll").write_bytes(b"x")
    return tmp_path.resolve()


def test_loaded_llama_cpp_blocks_vulkan_and_keeps_env(monkeypatch, tmp_path):
    import sys
    import types

    from tinglongwu import engines, llamacheck

    pack = _fake_pack(tmp_path)
    monkeypatch.setattr(mb, "vulkan_pack_dir", lambda base=None: pack)
    monkeypatch.setattr(mb, "vulkan_device_present", lambda *a: True)
    monkeypatch.delenv("LLAMA_CPP_LIB_PATH", raising=False)
    monkeypatch.delenv("TLWU_DEVICE", raising=False)
    fake = types.ModuleType("llama_cpp")
    fake.Llama = lambda **kw: kw
    monkeypatch.setitem(sys.modules, "llama_cpp", fake)  # already imported: cannot become the Vulkan build
    monkeypatch.setattr(llamacheck, "import_llama", lambda: fake)
    monkeypatch.setattr(llamacheck, "check_build", lambda m: None)
    engines.load_llama(tmp_path / "m.gguf", gpu=False)
    assert mb.last_backend == "cpu"
    assert "LLAMA_CPP_LIB_PATH" not in __import__("os").environ


def test_vulkan_import_failure_restores_env_and_uses_cpu(monkeypatch, tmp_path, capsys):
    import os
    import sys
    import types

    from tinglongwu import engines, llamacheck

    pack = _fake_pack(tmp_path)
    monkeypatch.setattr(mb, "vulkan_pack_dir", lambda base=None: pack)
    monkeypatch.setattr(mb, "vulkan_device_present", lambda *a: True)
    monkeypatch.delenv("LLAMA_CPP_LIB_PATH", raising=False)
    monkeypatch.delenv("TLWU_DEVICE", raising=False)
    monkeypatch.delitem(sys.modules, "llama_cpp", raising=False)
    fake = types.ModuleType("llama_cpp")
    fake.Llama = lambda **kw: kw
    seen = []

    def imp():
        seen.append(os.environ.get("LLAMA_CPP_LIB_PATH"))
        if len(seen) == 1:
            raise OSError("vulkan-1.dll dependency missing")
        sys.modules["llama_cpp"] = fake  # a successful import registers the module
        return fake

    monkeypatch.setattr(llamacheck, "import_llama", imp)
    monkeypatch.setattr(llamacheck, "check_build", lambda m: None)
    monkeypatch.setitem(sys.modules, "llama_cpp", fake)  # restored by monkeypatch afterwards
    monkeypatch.delitem(sys.modules, "llama_cpp")  # not imported at call time
    engines.load_llama(tmp_path / "m.gguf", gpu=False)
    assert seen == [str(pack), None]
    assert mb.last_backend == "cpu"
    assert "Vulkan llama.cpp build not loadable" in capsys.readouterr().err


def _pack(tmp_path, version):
    d = tmp_path / "vulkan-pack"
    d.mkdir()
    (d / "llama.dll").write_bytes(b"x")
    if version is not None:
        (d / mb.VERSION_FILE).write_text(version + "\n", "utf-8")
    return d


def test_pack_with_matching_version_is_used(tmp_path):
    from tinglongwu import __version__

    d = _pack(tmp_path, __version__)
    assert mb.vulkan_pack_dir(d, warn=lambda m: pytest.fail(m)) == d.resolve()


def test_stale_pack_version_falls_back_with_one_line(tmp_path):
    from tinglongwu import __version__

    d = _pack(tmp_path, "0.0.1")
    lines = []
    assert mb.vulkan_pack_dir(d, warn=lines.append) is None
    assert len(lines) == 1 and "0.0.1" in lines[0] and __version__ in lines[0] and "--vulkan-pack" in lines[0]


def test_pack_without_version_file_is_not_used(tmp_path):
    d = _pack(tmp_path, None)
    lines = []
    assert mb.vulkan_pack_dir(d, warn=lines.append) is None
    assert len(lines) == 1
