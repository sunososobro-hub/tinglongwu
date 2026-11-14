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
    assert mb.vulkan_pack_dir(tmp_path) == tmp_path.resolve()
    assert mb.vulkan_available(pack=lambda: tmp_path, present=lambda: True)
    assert not mb.vulkan_available(pack=lambda: None, present=lambda: True)
    assert not mb.vulkan_available(pack=lambda: tmp_path, present=lambda: False)


def test_selftest_reports_mt_backend():
    r = Result(ok=True, mt_backend="vulkan")
    assert "mt: vulkan" in r.text()
