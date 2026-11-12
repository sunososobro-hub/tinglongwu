"""#398: scripts/check_dist_dlls.py resolves every DLL dependent of a dist folder; the pack script ships the CUDA
runtime DLLs that llama.cpp needs and a CPU llama build; the app falls back to it when CUDA cannot load."""
import importlib.util
import io
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_clean_machine import make_pe  # noqa: E402

spec = importlib.util.spec_from_file_location("check_dist_dlls", ROOT / "scripts" / "check_dist_dlls.py")
cdd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cdd)
spec = importlib.util.spec_from_file_location("build_windows", ROOT / "packaging" / "build_windows.py")
bw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bw)

SYSTEM = {"kernel32.dll", "msvcp140.dll", "vcomp140.dll"}


def build(tmp_path, files):
    app = tmp_path / "tlwu"
    for rel, imports in files.items():
        p = app / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(make_pe(imports) if imports is not None else b"text")
    return app


# the #398 package: CUDA llama.cpp in llama_cpp/lib, no cuda_libs/
LLAMA_398 = {
    "tlwu.exe": ["KERNEL32.dll"],
    "llama_cpp/lib/llama.dll": ["ggml.dll", "ggml-base.dll", "KERNEL32.dll", "MSVCP140.dll",
                                "api-ms-win-crt-runtime-l1-1-0.dll"],
    "llama_cpp/lib/ggml.dll": ["ggml-cpu.dll", "ggml-cuda.dll", "ggml-base.dll"],
    "llama_cpp/lib/ggml-base.dll": ["KERNEL32.dll", "VCOMP140.DLL"],
    "llama_cpp/lib/ggml-cpu.dll": ["ggml-base.dll"],
    "llama_cpp/lib/ggml-cuda.dll": ["ggml-base.dll", "cudart64_12.dll", "cublas64_12.dll", "nvcuda.dll"],
}


def test_resolve_search_order_and_categories(tmp_path):
    app = tmp_path / "app"
    lib = app / "llama_cpp" / "lib"
    listing = {lib: {"ggml.dll"}, app: {"python312.dll"}, app / "cuda_libs": {"cudart64_12.dll"}}
    dirs = cdd.search_dirs(app, lib / "llama.dll")
    assert dirs[:3] == [lib, app, app / "cuda_libs"] and len(dirs) == len(set(dirs))
    assert cdd.resolve("GGML.dll", dirs, listing, SYSTEM) == str(lib)  # case-insensitive, own folder first
    assert cdd.resolve("python312.dll", dirs, listing, SYSTEM) == str(app)
    assert cdd.resolve("cudart64_12.dll", dirs, listing, SYSTEM) == str(app / "cuda_libs")
    assert cdd.resolve("KERNEL32.dll", dirs, listing, SYSTEM) == "system"
    assert cdd.resolve("kernel32", dirs, listing, {"kernel32"}) == "system"
    assert cdd.resolve("api-ms-win-crt-heap-l1-1-0.dll", dirs, listing, set()) == "apiset"
    assert cdd.resolve("nvcuda.dll", dirs, listing, SYSTEM) == "driver"
    assert cdd.resolve("cublas64_12.dll", dirs, listing, SYSTEM) is None


def test_a_dll_in_an_unsearched_folder_does_not_count(tmp_path):
    app = build(tmp_path, {"tlwu.exe": ["KERNEL32.dll"], "a/x.pyd": ["helper.dll"], "b/helper.dll": []})
    missing, _ = cdd.unresolved(app, SYSTEM)
    assert missing == {"helper.dll": ["a/x.pyd"]}
    (app / "numpy.libs").mkdir()  # delvewheel folders are registered by the package at import time
    (app / "numpy.libs" / "helper.dll").write_bytes(make_pe([]))
    assert cdd.unresolved(app, SYSTEM)[0] == {}


def test_the_398_package_fails_with_the_cuda_runtime_dlls(tmp_path):
    app = build(tmp_path, LLAMA_398)
    out = io.StringIO()
    assert cdd.check(app, [], out) == 1  # [] = built-in clean-Windows list (no VC++ runtime there either)
    assert "MISSING: cudart64_12.dll <- llama_cpp/lib/ggml-cuda.dll" in out.getvalue()
    missing, driver = cdd.unresolved(app, SYSTEM)
    assert set(missing) == {"cudart64_12.dll", "cublas64_12.dll"}
    assert missing["cudart64_12.dll"] == ["llama_cpp/lib/ggml-cuda.dll"]
    assert set(driver) == {"nvcuda.dll"}


def test_check_passes_once_cuda_libs_ship(tmp_path):
    app = build(tmp_path, {**LLAMA_398, "cuda_libs/cudart64_12.dll": ["KERNEL32.dll"],
                           "cuda_libs/cublas64_12.dll": ["cublasLt64_12.dll"], "cuda_libs/cublasLt64_12.dll": []})
    sysdir = tmp_path / "System32"
    sysdir.mkdir()
    for n in ("KERNEL32.dll", "MSVCP140.dll", "VCOMP140.DLL"):
        (sysdir / n).write_bytes(b"")
    out = io.StringIO()
    assert cdd.main([str(app), "--system-dir", str(sysdir)]) == 0
    assert cdd.check(app, [sysdir], out) == 0
    assert "check_dist_dlls: OK (0 unresolved" in out.getvalue() and "driver (NVIDIA GPU only): nvcuda.dll" in out.getvalue()
    (sysdir / "MSVCP140.dll").unlink()
    out = io.StringIO()
    assert cdd.check(app, [sysdir], out) == 1
    assert "MISSING: msvcp140.dll <- llama_cpp/lib/llama.dll" in out.getvalue()


def test_check_rejects_a_missing_folder(tmp_path):
    out = io.StringIO()
    assert cdd.check(tmp_path / "nope", [], out) == 1


# ---- pack script (#398: cuda_libs were silently not copied, the default source was the project .venv only)

def nvidia_site(tmp_path, wheels):
    sp = tmp_path / "site-packages"
    for wheel, names in wheels.items():
        b = sp / "nvidia" / wheel / "bin"
        b.mkdir(parents=True)
        for n in names:
            (b / n).write_bytes(b"x")
    return sp


FULL = {"cublas": ["cublas64_12.dll", "cublasLt64_12.dll"], "cuda_runtime": ["cudart64_12.dll"],
        "cuda_nvrtc": ["nvrtc64_120_0.dll"], "cudnn": ["cudnn64_9.dll", "cudnn_ops64_9.dll"]}


def test_find_cuda_src_skips_folders_without_the_wheels(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    partial = nvidia_site(tmp_path / "p", {"cublas": FULL["cublas"], "cudnn": FULL["cudnn"]})
    good = nvidia_site(tmp_path / "g", FULL)
    assert bw.find_cuda_src([empty, partial, good]) == good
    assert bw.find_cuda_src([empty, partial]) is None


def test_copy_cuda_copies_what_llama_needs(tmp_path):
    src = nvidia_site(tmp_path, {**FULL, "cublas": FULL["cublas"] + ["nvblas64_12.dll"]})
    app = tmp_path / "app"
    app.mkdir()
    bw.copy_cuda(app, src)
    names = {f.name for f in (app / "cuda_libs").iterdir()}
    assert set(bw.LLAMA_CUDA_NEEDS) <= names and "cudnn_ops64_9.dll" in names and "nvblas64_12.dll" not in names
    bad = nvidia_site(tmp_path / "bad", {**FULL, "cuda_runtime": []})
    (tmp_path / "app2").mkdir()
    with pytest.raises(SystemExit, match="cudart64_12"):
        bw.copy_cuda(tmp_path / "app2", bad)


def cpu_wheel(tmp_path):
    whl = tmp_path / "llama_cpp_python-0.3.36-py3-none-win_amd64.whl"
    with zipfile.ZipFile(whl, "w") as z:
        for n in ("ggml.dll", "ggml-base.dll", "ggml-cpu.dll", "llama.dll", "mtmd.dll"):
            z.writestr(f"llama_cpp/lib/{n}", b"cpu")
        z.writestr("bin/llama-common.dll", b"cpu")
        z.writestr("llama_cpp/llama.py", b"")
    return whl


def test_cpu_llama_dlls_from_a_local_wheel(tmp_path, monkeypatch):
    monkeypatch.setenv("TLWU_LLAMA_CPU_WHEEL", str(cpu_wheel(tmp_path)))
    dlls = bw.cpu_llama_dlls(tmp_path / "cache", run=lambda *a, **k: pytest.fail("no download with a local wheel"))
    assert sorted(f.name for f in dlls) == ["ggml-base.dll", "ggml-cpu.dll", "ggml.dll", "llama.dll", "mtmd.dll"]


def test_cpu_llama_dlls_downloads_the_cpu_wheel_once(tmp_path, monkeypatch):
    monkeypatch.delenv("TLWU_LLAMA_CPU_WHEEL", raising=False)
    cache, calls = tmp_path / "cache", []
    whl = cpu_wheel(tmp_path)

    def fake_pip(cmd):
        calls.append(cmd)
        (cache / whl.name).write_bytes(whl.read_bytes())

    bw.cpu_llama_dlls(cache, run=fake_pip)
    bw.cpu_llama_dlls(cache, run=fake_pip)
    assert len(calls) == 1 and calls[0][calls[0].index("--index-url") + 1].endswith("/whl/cpu")


def test_copy_llama_ships_a_cpu_fallback_and_lite_is_cpu_only(tmp_path):
    src = tmp_path / "venv-lib"
    src.mkdir()
    for n in ("ggml.dll", "ggml-cuda.dll", "llama.dll"):
        (src / n).write_bytes(b"cuda")
    cpu = tmp_path / "cpu"
    cpu.mkdir()
    for n in ("ggml.dll", "llama.dll"):
        (cpu / n).write_bytes(b"cpu")
    app = tmp_path / "app"
    (app / "cuda_libs").mkdir(parents=True)
    bw.copy_llama(app, lite=False, cpu_dlls=sorted(cpu.iterdir()), src=src)
    assert (app / "llama_cpp/lib/ggml-cuda.dll").read_bytes() == b"cuda"
    assert (app / "llama_cpp/lib_cpu/llama.dll").read_bytes() == b"cpu"
    bw.copy_llama(app, lite=True, cpu_dlls=sorted(cpu.iterdir()), src=src)
    assert sorted(f.name for f in (app / "llama_cpp/lib").iterdir()) == ["ggml.dll", "llama.dll"]
    assert (app / "llama_cpp/lib/llama.dll").read_bytes() == b"cpu"
    assert not (app / "llama_cpp/lib_cpu").exists() and not (app / "cuda_libs").exists()


def test_smoke_fails_the_pack_when_llama_does_not_load(tmp_path):
    class R:
        returncode, stderr = 0, ""
        stdout = "OK   numpy (0.0s)\nWARN llama_cpp: RuntimeError: Failed to load shared library\nsmoke: OK\n"

    with pytest.raises(SystemExit, match="llama_cpp"):
        bw.smoke(tmp_path, run=lambda *a, **k: R)
    R.stdout = "OK   llama_cpp (0.1s)\nsmoke: OK\n"
    bw.smoke(tmp_path, run=lambda *a, **k: R)


# ---- run time: the standalone build picks llama_cpp/lib_cpu when the CUDA llama.dll cannot load

@pytest.fixture
def bundle(tmp_path, monkeypatch):
    from tinglongwu import device

    cpu = tmp_path / "llama_cpp" / "lib_cpu"
    cpu.mkdir(parents=True)
    (cpu / "llama.dll").write_bytes(b"")
    monkeypatch.setattr(device, "bundled_llama_cpu_dir", lambda: cpu)
    monkeypatch.setattr(device, "register_cuda_libs", lambda: None)
    monkeypatch.setattr(device.sys, "platform", "win32")
    monkeypatch.delenv("LLAMA_CPP_LIB_PATH", raising=False)
    return device, cpu


def test_cpu_build_selected_without_the_nvidia_driver(bundle, monkeypatch):
    import os

    device, cpu = bundle

    def load(name):
        if name == "nvcuda.dll":
            raise OSError("not found")

    assert device.use_llama_cpu_build(load) is True
    assert os.environ["LLAMA_CPP_LIB_PATH"] == str(cpu)


def test_cuda_build_kept_when_everything_loads(bundle):
    import os

    device, _ = bundle
    assert device.use_llama_cpu_build(lambda name: None) is False
    assert "LLAMA_CPP_LIB_PATH" not in os.environ


def test_no_bundle_no_change(monkeypatch):
    import os

    from tinglongwu import device

    monkeypatch.setattr(device, "bundled_llama_cpu_dir", lambda: None)
    monkeypatch.delenv("LLAMA_CPP_LIB_PATH", raising=False)
    assert device.use_llama_cpu_build(lambda name: pytest.fail("no probing without a bundled CPU build")) is False
    assert "LLAMA_CPP_LIB_PATH" not in os.environ


def test_import_llama_retries_with_the_cpu_build(bundle, monkeypatch, caplog):
    import builtins
    import os
    import types

    from tinglongwu import llamacheck

    device, cpu = bundle
    monkeypatch.setattr(device, "use_llama_cpu_build", lambda: False)
    real_import, seen = builtins.__import__, []

    def fake_import(name, *a, **k):
        if name == "llama_cpp":
            seen.append(os.environ.get("LLAMA_CPP_LIB_PATH"))
            if len(seen) == 1:
                raise RuntimeError("Failed to load shared library llama.dll")
            return types.SimpleNamespace(__name__="llama_cpp")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(llamacheck, "cpu_fallback", None)
    with caplog.at_level("WARNING", logger="tinglongwu.llamacheck"):
        assert llamacheck.import_llama().__name__ == "llama_cpp"
    assert seen == [None, str(cpu)]
    assert "Failed to load shared library llama.dll" in llamacheck.cpu_fallback  # the CUDA error is kept
    assert "Failed to load shared library llama.dll" in caplog.text


def test_cpu_selection_keeps_the_reason(bundle, monkeypatch):
    device, _ = bundle

    def load(name):
        raise OSError("module not found")

    assert device.use_llama_cpu_build(load) is True
    assert "module not found" in device.llama_cpu_reason


def test_smoke_reports_the_cpu_fallback(monkeypatch):
    import tinglongwu.llamacheck as lc
    from tlwu_app import smoke as sm

    monkeypatch.setattr(lc, "cpu_fallback", None)
    monkeypatch.setitem(sm.IMPORTERS, "llama_cpp", lambda: setattr(lc, "cpu_fallback", "cudart64_12.dll: not found"))
    out = io.StringIO()
    sm.check([("llama_cpp", False)], out=out, lazy=[])
    assert "CPU  llama_cpp: CUDA build not used (cudart64_12.dll: not found)" in out.getvalue()


def test_pack_smoke_fails_on_the_cpu_fallback_unless_lite(tmp_path):
    class R:
        returncode, stderr = 0, ""
        stdout = "OK   llama_cpp (0.1s)\nCPU  llama_cpp: CUDA build not used (x); loaded llama_cpp/lib_cpu\nsmoke: OK\n"

    with pytest.raises(SystemExit, match="CUDA llama build did not load"):
        bw.smoke(tmp_path, run=lambda *a, **k: R)
    bw.smoke(tmp_path, run=lambda *a, **k: R, lite=True)
    bw.smoke(tmp_path, run=lambda *a, **k: R, no_gpu_ok=True)  # #414: CI runner without an NVIDIA driver


def test_allow_cpu_only_relaxes_the_smoke_only_without_an_nvidia_driver(tmp_path, monkeypatch):
    app = tmp_path / "dist" / "tlwu"
    app.mkdir(parents=True)
    (app / "tlwu.exe").write_bytes(b"")
    for name, val in (("DIST", tmp_path / "dist"), ("APP", app), ("find_cuda_src", lambda *a: tmp_path),
                      ("copy_llama", lambda *a, **k: None), ("cpu_llama_dlls", lambda *a, **k: []),
                      ("copy_cuda", lambda *a, **k: []), ("write_licenses", lambda *a, **k: None),
                      ("make_zip", lambda *a, **k: (tmp_path / "dist" / "tinglongwu-win64.zip").write_bytes(b"z")),
                      ("REPO", tmp_path)):
        monkeypatch.setattr(bw, name, val)
    (tmp_path / "packaging").mkdir()
    (tmp_path / "packaging" / "使用說明.txt").write_text("x", "utf-8")
    monkeypatch.setitem(sys.modules, "check_dist_dlls", type(sys)("check_dist_dlls"))
    sys.modules["check_dist_dlls"].check = lambda app: 0
    monkeypatch.setitem(sys.modules, "clean_machine_check", type(sys)("clean_machine_check"))
    sys.modules["clean_machine_check"].cmd_deps = lambda app: 0
    seen = []
    monkeypatch.setattr(bw, "smoke", lambda app, lite=False, no_gpu_ok=False: seen.append((lite, no_gpu_ok)))
    for flag, driver, want in (([], False, False), (["--allow-cpu-only"], False, True),
                               (["--allow-cpu-only"], True, False)):
        monkeypatch.setattr(bw, "has_nvidia_driver", lambda d=driver: d)
        monkeypatch.setattr(sys, "argv", ["build_windows.py", "--post-only", *flag])
        assert bw.main() == 0
        assert seen.pop() == (False, want)


def test_build_stops_without_cuda_wheels(tmp_path, monkeypatch):
    app = tmp_path / "dist" / "tlwu"
    app.mkdir(parents=True)
    (app / "tlwu.exe").write_bytes(b"")
    monkeypatch.setattr(bw, "DIST", tmp_path / "dist")
    monkeypatch.setattr(bw, "APP", app)
    monkeypatch.setattr(bw, "find_cuda_src", lambda *a: None)
    monkeypatch.setattr(bw, "copy_llama", lambda *a, **k: pytest.fail("must stop before packing"))
    monkeypatch.setattr(sys, "argv", ["build_windows.py", "--post-only"])
    with pytest.raises(SystemExit, match="allow-cpu-only"):
        bw.main()
