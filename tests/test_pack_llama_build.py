"""#865: the core zip has a CPU llama.cpp that never loads ggml-cuda.dll; the GPU pack's CUDA build must be used."""
from __future__ import annotations

import sys
import types

from tinglongwu import device, llamacheck


def _dirs(tmp_path, pack_files, lib_files, with_version=llamacheck.LLAMA_VERSION):
    pack, lib = tmp_path / "gpu_pack", tmp_path / "llama_cpp" / "lib"
    for d, files in ((pack, pack_files), (lib, lib_files)):
        d.mkdir(parents=True)
        for n in files:
            (d / n).write_bytes(b"x")
    if with_version is not None:
        (pack / device.PACK_LLAMA_VERSION_FILE).write_text(with_version + "\n")
    return pack, lib


def test_pack_with_cuda_llama_is_used_over_core_cpu_build(tmp_path):
    pack, lib = _dirs(tmp_path, ["llama.dll", "ggml.dll", "ggml-cuda.dll"], ["llama.dll", "ggml.dll"])
    assert device.pack_llama_dir(pack, lib) == pack


def test_pack_built_from_another_llama_version_is_not_used_and_logs_both(tmp_path, caplog):  # #881
    pack, lib = _dirs(tmp_path, ["llama.dll", "ggml.dll", "ggml-cuda.dll"], ["llama.dll", "ggml.dll"], with_version="0.3.35")
    with caplog.at_level("WARNING", logger="tinglongwu.device"):
        assert device.pack_llama_dir(pack, lib) is None
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 1 and "0.3.35" in msgs[0] and llamacheck.LLAMA_VERSION in msgs[0]


def test_pack_without_version_file_is_not_used(tmp_path):  # #881
    pack, lib = _dirs(tmp_path, ["llama.dll", "ggml.dll", "ggml-cuda.dll"], ["llama.dll", "ggml.dll"], with_version=None)
    assert device.pack_llama_dir(pack, lib) is None


def test_pack_without_ggml_dll_is_not_used(tmp_path):  # #881: a half-copied pack
    pack, lib = _dirs(tmp_path, ["llama.dll", "ggml-cuda.dll"], ["llama.dll", "ggml.dll"])
    assert device.pack_llama_dir(pack, lib) is None


def test_old_pack_with_only_ggml_cuda_is_not_used(tmp_path):
    pack, lib = _dirs(tmp_path, ["ggml-cuda.dll", "cublas64_12.dll"], ["llama.dll", "ggml.dll"])
    assert device.pack_llama_dir(pack, lib) is None


def test_full_build_with_cuda_lib_ignores_pack(tmp_path):
    pack, lib = _dirs(tmp_path, ["llama.dll", "ggml-cuda.dll"], ["llama.dll", "ggml-cuda.dll"])
    assert device.pack_llama_dir(pack, lib) is None


def test_no_pack(tmp_path):
    _, lib = _dirs(tmp_path, [], ["llama.dll"])
    assert device.pack_llama_dir(tmp_path / "missing", lib) is None


def test_use_pack_sets_lib_path_and_import_falls_back_to_cpu(tmp_path, monkeypatch):
    pack, lib = _dirs(tmp_path, ["llama.dll", "ggml-cuda.dll"], ["llama.dll"])
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("LLAMA_CPP_LIB_PATH", raising=False)
    monkeypatch.setattr(device, "pack_llama_dir", lambda *a, **k: pack)
    monkeypatch.setattr(device, "register_cuda_libs", lambda: None)
    monkeypatch.setattr(device, "use_llama_cpu_build", lambda *a, **k: False)
    monkeypatch.setattr(device, "llama_backend", "default")
    monkeypatch.setattr(llamacheck, "cpu_fallback", None)
    seen = []

    mod = types.ModuleType("llama_cpp")
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake(name, *a, **k):
        if name == "llama_cpp":
            import os

            seen.append(os.environ.get("LLAMA_CPP_LIB_PATH"))
            if os.environ.get("LLAMA_CPP_LIB_PATH") == str(pack):
                raise OSError("ggml-cuda.dll: CUDA driver missing")
            return mod
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", fake)
    assert llamacheck.import_llama() is mod
    monkeypatch.undo()
    assert seen == [str(pack), None]
