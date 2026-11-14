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


def _llama(root: Path, mod, with_ggml=True, version="0.3.36"):
    lib = root / "llama_cpp" / "lib"
    lib.mkdir(parents=True)
    (lib.parent / "__init__.py").write_text(f'from .llama_cpp import *\n\n__version__ = "{version}"\n')
    if with_ggml:
        for n in (mod.GPU_PACK_LLAMA_DLL, "llama.dll", "ggml.dll", "ggml-base.dll"):
            (lib / n).write_bytes(b"cuda-" + n.encode())
    return lib


def test_builds_zip_and_manifest_without_nuitka(tmp_path, monkeypatch):
    m = _mod()
    monkeypatch.setattr(m, "run_nuitka", lambda *a, **k: pytest.fail("nuitka must not run"))
    sp, lib = _licenses(_wheels(tmp_path / "sp", m), m), _llama(tmp_path, m)
    (tmp_path / "LICENSE.md").write_text("MIT")
    man_path = m.build_gpu_pack_only(tmp_path / "out", cuda_src=sp, llama_lib=lib, url="https://x/p.zip",
                                     llama_license=tmp_path / "LICENSE.md")
    zp = tmp_path / "out" / m.GPU_PACK_ASSET
    assert m.GPU_PACK_ASSET == "tinglongwu-gpu-pack.zip" and zp.is_file()
    with zipfile.ZipFile(zp) as z:
        names = set(z.namelist())
    assert set(m.LLAMA_CUDA_NEEDS) | {"ggml-cuda.dll", "cudnn64_9.dll", "nvrtc64_120_0.dll"} <= names
    assert {"llama.dll", "ggml.dll", "ggml-base.dll"} <= names  # #865: the CUDA build replaces the core's CPU one
    with zipfile.ZipFile(zp) as z:
        assert z.read("llama_version.txt").decode().strip() == "0.3.36"  # #881: stamped from the CUDA wheel
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


# ---- #764 Opus CHANGES A1 / A4

def _licenses(sp: Path, mod):
    for name in mod.NVIDIA_LICENSE_WHEELS:
        d = sp / f"nvidia_{name}_cu12-12.9.0.dist-info" / "licenses"
        d.mkdir(parents=True)
        (d / "License.txt").write_text(f"{name} EULA")
    return sp


def test_gpu_pack_ships_nvidia_licenses_and_flat_dlls(tmp_path):
    m = _mod()
    sp = _licenses(_wheels(tmp_path / "sp", m), m)
    llama_lic = tmp_path / "LICENSE.md"
    llama_lic.write_text("MIT")
    m.build_gpu_pack_only(tmp_path / "out", cuda_src=sp, llama_lib=_llama(tmp_path, m), llama_license=llama_lic)
    with zipfile.ZipFile(tmp_path / "out" / m.GPU_PACK_ASSET) as z:
        names = set(z.namelist())
    for name in m.NVIDIA_LICENSE_WHEELS:
        assert f"licenses/NVIDIA-{name}-License.txt" in names
    assert "licenses/LICENSE-llama.cpp-LICENSE.md" in names
    assert all("/" not in n for n in names if n.lower().endswith(".dll"))  # DLLs stay at the zip root


def test_gpu_pack_without_nvidia_licenses_exits(tmp_path):
    m = _mod()
    with pytest.raises(SystemExit, match="NVIDIA license"):
        m.build_gpu_pack_only(tmp_path / "out", cuda_src=_wheels(tmp_path / "sp", m), llama_lib=_llama(tmp_path, m))


def test_release_yml_nvrtc_pin_matches_gpu_pack_nvrtc():
    import re

    text = (SRC.parent.parent / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    pin = re.search(r"nvidia-cuda-nvrtc-cu12==(\d+)\.(\d+)\.", text)
    assert pin, "release.yml no longer pins nvidia-cuda-nvrtc-cu12"
    m = _mod()
    builtins = f"nvrtc-builtins64_{pin.group(1)}{pin.group(2)}.dll"
    assert builtins in m.GPU_PACK_NVRTC and builtins in m.CUDA_WHEELS["cuda_nvrtc"]


def test_pack_carries_the_cuda_wheels_llama_version_and_selection_matches_it(tmp_path):  # #881
    from tinglongwu import device, llamacheck

    m = _mod()
    llama_lic = tmp_path / "LICENSE.md"
    llama_lic.write_text("MIT")
    man_path = m.build_gpu_pack_only(tmp_path / "out", cuda_src=_licenses(_wheels(tmp_path / "sp", m), m),
                                     llama_lib=_llama(tmp_path, m, version=llamacheck.LLAMA_VERSION),
                                     llama_license=llama_lic)
    assert man_path.is_file()
    with zipfile.ZipFile(tmp_path / "out" / m.GPU_PACK_ASSET) as z:
        z.extractall(tmp_path / "pack")
    core_lib = tmp_path / "core_lib"
    core_lib.mkdir()
    assert device.pack_llama_dir(tmp_path / "pack", core_lib) == tmp_path / "pack"
    (tmp_path / "pack" / device.PACK_LLAMA_VERSION_FILE).write_text("0.0.1\n")  # a core update bumped LLAMA_VERSION
    assert device.pack_llama_dir(tmp_path / "pack", core_lib) is None


def test_unreadable_llama_wheel_version_exits_and_explicit_version_wins(tmp_path, monkeypatch):
    m = _mod()
    lib = tmp_path / "x" / "lib"
    lib.mkdir(parents=True)
    monkeypatch.setattr(m.md, "version", lambda n: (_ for _ in ()).throw(m.md.PackageNotFoundError(n)))
    root = tmp_path / "y"
    root.mkdir()
    assert m.llama_wheel_version(_llama(root, m, version="0.3.40")) == "0.3.40"
    with pytest.raises(SystemExit, match="llama-cpp-python version"):
        m.llama_wheel_version(lib)
