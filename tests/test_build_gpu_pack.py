"""#763: build_windows.py --gpu-pack-only builds the GPU pack from the CUDA wheels without Nuitka."""
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

from tinglongwu import gpu_pack

SRC = Path(__file__).resolve().parent.parent / "packaging" / "build_windows.py"


def _mod():
    spec = importlib.util.spec_from_file_location("build_windows", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _wheels(root: Path, mod, skip=()):
    for wheel, names in mod.CUDA_WHEELS.items():
        b = root / "nvidia" / wheel / "bin"
        b.mkdir(parents=True)
        for n in (names or ["cudnn64_9.dll", "cudnn_ops64_9.dll"]):
            if n not in skip:
                (b / n).write_bytes(b"dll-" + n.encode())
    return root


def _llama(root: Path, mod, with_ggml=True):
    lib = root / "llama_lib"
    lib.mkdir()
    if with_ggml:
        (lib / mod.GPU_PACK_LLAMA_DLL).write_bytes(b"ggml")
    return lib


def test_builds_zip_and_manifest_without_nuitka(tmp_path, monkeypatch):
    m = _mod()
    monkeypatch.setattr(m, "run_nuitka", lambda *a, **k: pytest.fail("nuitka must not run"))
    sp, lib = _wheels(tmp_path / "sp", m), _llama(tmp_path, m)
    man_path = m.build_gpu_pack_only(tmp_path / "out", cuda_src=sp, llama_lib=lib, url="https://x/p.zip")
    zp = tmp_path / "out" / m.GPU_PACK_ASSET
    assert m.GPU_PACK_ASSET == "tinglongwu-gpu-pack.zip" and zp.is_file()
    with zipfile.ZipFile(zp) as z:
        names = set(z.namelist())
    assert set(m.LLAMA_CUDA_NEEDS) | {"ggml-cuda.dll", "cudnn64_9.dll", "nvrtc64_120_0.dll"} <= names
    man = gpu_pack.load_manifest(man_path)
    assert all(k in man for k in gpu_pack.MANIFEST_KEYS) and man["wheels_key"] == m.gpu_pack_wheels_key()
    gpu_pack.verify(zp, man)


def test_missing_dll_exits_naming_it(tmp_path):
    m = _mod()
    sp = _wheels(tmp_path / "sp", m, skip=("cublasLt64_12.dll",))
    with pytest.raises(SystemExit, match="cublasLt64_12.dll"):
        m.build_gpu_pack_only(tmp_path / "out", cuda_src=sp, llama_lib=_llama(tmp_path, m))


def test_missing_ggml_cuda_exits_naming_it(tmp_path):
    m = _mod()
    with pytest.raises(SystemExit, match="ggml-cuda.dll"):
        m.build_gpu_pack_only(tmp_path / "out", cuda_src=_wheels(tmp_path / "sp", m),
                              llama_lib=_llama(tmp_path, m, with_ggml=False))


def test_wheels_key_changes_with_requirements(tmp_path):
    m = _mod()
    for n in m.GPU_PACK_KEY_FILES:
        (tmp_path / n).write_text("a")
    k1 = m.gpu_pack_wheels_key(tmp_path)
    (tmp_path / m.GPU_PACK_KEY_FILES[0]).write_text("b")
    assert m.gpu_pack_wheels_key(tmp_path) != k1


def test_old_manifest_without_wheels_key_still_loads_and_verifies(tmp_path):
    z = tmp_path / "p.zip"
    z.write_bytes(b"x")
    man = gpu_pack.make_manifest(z, "u")
    mp = tmp_path / "m.json"
    mp.write_text(json.dumps(man))
    loaded = gpu_pack.load_manifest(mp)
    assert "wheels_key" not in loaded
    gpu_pack.verify(z, loaded)
