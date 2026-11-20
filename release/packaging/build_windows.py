r"""Build the standalone Windows folder app (tlwu.exe) in two layers and zip it (#923).

    python release\packaging\build_windows.py --lite            CPU-only core (CI, releases): makes its own packaging venv
    <pack-venv>\Scripts\python.exe release\packaging\build_windows.py [--allow-cpu-only]    CUDA variant (pack machine)

Runtime layer (release/packaging/runtime_layer.py): tlwu.exe launcher + Python + every third-party package (.pyc) + llama.cpp
DLLs + VC++ runtime + licenses, kept in <build root>/runtime/tlwu. It is rebuilt only when runtime_layer.key() changes
(requirements*.txt or the packaging code); CI restores it from a cache. Building it needs a packaging venv =
requirements.txt (without av / psutil) + a llama-cpp-python wheel: --lite makes that venv itself (CPU wheel) when this
interpreter is not one; the CUDA variant must be started from a pack-venv that has requirements-llama-cuda.txt.
App layer: tinglongwu/, tlwu_app/, tinglongwu_setup/ as .py + .pyc, repacked on every run (seconds).
No compiler runs over Python code, so a one-line app change builds in about a minute instead of a Nuitka recompile.

PyAV is replaced by release/packaging/stubs/av because its wheel bundles GPL FFmpeg codecs (libx264/libx265).
CUDA runtime DLLs are copied from the nvidia-*-cu12 wheels (requirements-gpu.txt), found in TLWU_CUDA_SRC, the
pack-venv or the project .venv (read only). None found -> the build stops (--lite / --allow-cpu-only: CPU-only); never a
package whose CUDA llama.dll cannot load (#398). A CPU llama.cpp build (the llama-cpp-python CPU wheel, downloaded
once) goes to llama_cpp/lib_cpu/: the app uses it when the CUDA llama.dll cannot load (no NVIDIA GPU / driver).
Steps: runtime layer (reused or built) -> dist/tlwu = runtime layer + app layer -> scripts/check_dist_dlls.py (every
DLL dependent resolves, #398) -> `tlwu.exe --smoke` (#310; --no-smoke skips) -> dist/tinglongwu-win64.zip.
"""
from __future__ import annotations

import argparse
import compileall
import hashlib
import importlib.metadata as md
import json
import os
import py_compile
import re
import shutil
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # release/<dir>/this.py (#1653)
# src/ layer layout (#1664): tinglongwu (gpu_pack, llamacheck) is imported from src/core; its __init__ adds the
# other layer dirs. Set here, not in the pack-recipe functions, so the GPU pack recipe fingerprint is unchanged.
if str(REPO / "src" / "core") not in sys.path:
    sys.path.insert(0, str(REPO / "src" / "core"))
EVAL = Path(os.environ.get("TLWU_BUILD_ROOT", str(Path.home() / "tlwu-eval")))
BUILD = EVAL / "build"
RUNTIME = EVAL / "runtime" / "tlwu"  # the runtime layer (release/packaging/runtime_layer.py); CI caches EVAL/runtime
PACK_VENV = BUILD / "pack-venv"      # made by --lite when this interpreter is not a packaging venv
APP_ICON = REPO / "src" / "ui" / "tinglongwu" / "gui" / "assets" / "tinglongwu.ico"  # same icon as the wizard and shortcuts (#127)


def read_version() -> str:
    """tinglongwu.__version__ is the single source of truth (read as text: no import, so no offline guard side effects)."""
    import re

    m = re.search(r'^__version__\s*=\s*"([^"]+)"', (REPO / "src" / "core" / "tinglongwu" / "__init__.py").read_text("utf-8"), re.M)
    if not m:
        raise SystemExit("tinglongwu/__init__.py has no __version__")
    return m.group(1)


def win_version(v: str) -> str:
    """'0.3.1' -> '0.3.1.0' (Windows file/product version has four numeric parts)."""
    parts = (v.split("+")[0].split(".") + ["0"] * 4)[:4]
    return ".".join(p if p.isdigit() else "0" for p in parts)


WIN_VERSION = win_version(read_version())
DIST = EVAL / "dist"
APP = DIST / "tlwu"
CUDA_SRC_CANDIDATES = [Path(p) for p in (os.environ.get("TLWU_CUDA_SRC"),) if p] + [
    Path(sys.prefix) / "Lib" / "site-packages", REPO / ".venv" / "Lib" / "site-packages"]
LLAMA_CPU = EVAL / "build" / "llama-cpu"  # CPU wheel download cache

# cuBLAS/cuDNN for CTranslate2 (Whisper) and cudart/cuBLAS for llama.cpp (ggml-cuda.dll)
CUDA_WHEELS = {"cublas": ["cublas64_12.dll", "cublasLt64_12.dll"],  # nvblas64_12.dll is not needed
               "cuda_runtime": ["cudart64_12.dll"],
               "cuda_nvrtc": ["nvrtc64_120_0.dll", "nvrtc-builtins64_129.dll"],  # cuDNN runtime-compiled engines
               "cudnn": None}  # None = all DLLs
# ggml-cuda.dll imports cudart + cuBLAS (cuBLAS imports cuBLASLt); missing -> "Could not find module llama.dll" (#398)
LLAMA_CUDA_NEEDS = ("cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll")
# Standalone GPU pack (#763, part 2 of #740): built from the CUDA wheels without Nuitka.
GPU_PACK_ASSET = "tinglongwu-gpu-pack.zip"
GPU_PACK_LLAMA_DLL = "ggml-cuda.dll"  # from the pack-venv's llama_cpp/lib (CUDA build of llama-cpp-python)
GPU_PACK_NVRTC = "nvrtc64_120_0.dll+nvrtc-builtins64_129.dll"  # the nvrtc version CUDA_WHEELS ships (not pinned in requirements)
GPU_PACK_KEY_FILES = ("requirements-gpu.txt", "requirements-llama-cuda.txt")  # their sha256 + GPU_PACK_NVRTC = wheels_key
# nvidia-*-cu12 wheels whose licenses/ ship with any package that redistributes their DLLs (full zip, GPU pack)
NVIDIA_LICENSE_WHEELS = ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc")
GPU_PACK_LICENSE_DIR = "licenses"  # zip folder for license texts; DLLs stay at the root (gpu_pack.pack_dir)
# Microsoft VC++ 2015-2022 runtime, absent on a clean Windows: ctranslate2 / llama.cpp import msvcp140, onnxruntime
# msvcp140_1, ggml vcomp140 (OpenMP). Copied as one matched set from the installed redistributable (System32) next
# to tlwu.exe, replacing the older vcruntime140 that ships with Python: msvcp140 must not be newer than vcruntime140.
VC_RUNTIME = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "vcomp140.dll")
# The bundled copy is used even where Windows has a newer one: an msvcp140 older than the toolset a wheel was built
# with crashes (VS 17.10 std::mutex change), so the pack machine's redistributable must be at least 14.40.
VC_MIN_VERSION = (14, 40)


def pe_file_version(data: bytes) -> tuple[int, int, int, int] | None:
    """FileVersion of a PE file from its VS_FIXEDFILEINFO resource (signature 0xFEEF04BD), None if it has none."""
    i = data.find(b"\xbd\x04\xef\xfe")
    if i < 0 or len(data) < i + 16:
        return None
    ms, ls = struct.unpack_from("<II", data, i + 8)
    return ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF


def pe_machine(data: bytes) -> int | None:
    """IMAGE_FILE_HEADER.Machine (0x8664 = x64), None if not a PE file."""
    if data[:2] != b"MZ" or len(data) < 0x40:
        return None
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    return struct.unpack_from("<H", data, pe + 4)[0] if data[pe:pe + 4] == b"PE\0\0" else None


PE_SUBSYSTEM_WINDOWS_GUI = 2
PE_SUBSYSTEM_WINDOWS_CUI = 3
OPTIONAL_HEADER_SUBSYSTEM_OFFSET = 68  # IMAGE_OPTIONAL_HEADER.Subsystem (same offset in PE32 and PE32+)
FILE_HEADER_SIZE = 20


def pe_subsystem(data: bytes) -> int | None:
    """IMAGE_OPTIONAL_HEADER.Subsystem (2 = WINDOWS GUI, 3 = console), None if not a PE file (#1524)."""
    if pe_machine(data) is None:
        return None
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    off = pe + 4 + FILE_HEADER_SIZE + OPTIONAL_HEADER_SUBSYSTEM_OFFSET
    return struct.unpack_from("<H", data, off)[0] if len(data) >= off + 2 else None


def has_nvidia_driver() -> bool:
    """nvcuda.dll (installed by the NVIDIA display driver) is present: a CUDA llama.dll is expected to load here."""
    return (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvcuda.dll").is_file()


def smoke(app: Path, run=subprocess.run, lite: bool = False, no_gpu_ok: bool = False) -> None:
    """Run the built `tlwu.exe --smoke` (no models, no network): native DLLs and the lazy filelock /
    huggingface_hub imports of the first-run download (#310). A failure stops the build before the zip.
    llama_cpp is optional in the smoke (a clean PC may lack the VC++ runtime), but not on the pack machine (#398)."""
    r = run([str(app / "tlwu.exe"), "--smoke"], cwd=app, capture_output=True, text=True, timeout=300)
    print(r.stdout, end="")
    if r.returncode != 0:
        raise SystemExit(f"tlwu.exe --smoke failed (exit {r.returncode}), not zipping:\n{r.stdout}{r.stderr}")
    if "WARN llama_cpp" in r.stdout:
        raise SystemExit("tlwu.exe --smoke: llama_cpp does not load in the package (#398), not zipping")
    if not lite and "CPU  llama_cpp" in r.stdout:  # the CUDA llama.dll is broken; the CPU fallback must not hide it
        if no_gpu_ok:  # CI runner (#414): no NVIDIA driver, so CUDA cannot load; check_dist_dlls covers the CUDA deps
            print("WARNING: CUDA llama build not loaded: no NVIDIA driver on this machine (--allow-cpu-only). "
                  "The CPU fallback works; the GPU path is NOT validated by this build")
            return
        raise SystemExit("tlwu.exe --smoke: the CUDA llama build did not load on the pack machine (CPU line above), "
                         "not zipping")


NVIDIA_NOTE = """This package includes NVIDIA CUDA and cuDNN libraries (cuda_libs/) redistributed under the NVIDIA CUDA Toolkit EULA
and the cuDNN Software License Agreement; see the NVIDIA-*.txt files in this folder.
"""
NOTICES_HEAD = """tinglongWu itself is MIT licensed: https://github.com/sunososobro-hub/tinglongwu

Models are NOT bundled; they are downloaded on first run (Whisper: MIT, Opus-MT: CC-BY-4.0, Argos Translate: MIT,
Silero VAD: MIT, Hy-MT2: Apache-2.0).
Microsoft Visual C++ runtime files (vcruntime140.dll, vcruntime140_1.dll, msvcp140.dll, msvcp140_1.dll, vcomp140.dll
in this folder, from the Visual C++ 2015-2022 x64 runtime): redistributable under the Microsoft Visual Studio license
terms (Distributable Code). If tlwu.exe still does not start, install the Microsoft Visual C++ Redistributable
2015-2022 (x64).
""" + NVIDIA_NOTE + """No GPL components: PyAV/FFmpeg is NOT included (stubbed out).

Native components and their licenses
- libsndfile (_soundfile_data/libsndfile_x64.dll): LGPL-2.1, full text in LICENSE-libsndfile-LGPL-2.1.txt.
  Source: https://github.com/libsndfile/libsndfile . It is a separate DLL; you may replace it with your own build
  of libsndfile (same ABI) by overwriting that file.
- llama.cpp / ggml (llama_cpp/lib/*.dll): MIT, Copyright (c) The ggml authors. https://github.com/ggml-org/llama.cpp
- Python runtime (python3*.dll, python3*.zip = the standard library, DLLs/*.pyd): Python Software Foundation
  License, full text (with the licenses of the software CPython incorporates) in LICENSE-Python.txt.
  https://docs.python.org/3/license.html
- tlwu.exe (the launcher, release/packaging/launcher in the source): MIT like the rest of tinglongWu. When built with
  MinGW-w64 it links the MinGW-w64 runtime (public domain / Zope Public License 2.1 parts, redistributable without
  further conditions on the binary), text in COPYING.MinGW-w64-runtime.txt. https://www.mingw-w64.org/
- Tcl/Tk (DLLs/tcl86t.dll, DLLs/tk86t.dll, tcl/): Tcl/Tk license (BSD-style).
  https://www.tcl.tk/software/tcltk/license.html
- OpenSSL (DLLs/libcrypto-3.dll, DLLs/libssl-3.dll): Apache-2.0. https://www.openssl.org/
- libffi (DLLs/libffi-8.dll): MIT. https://github.com/libffi/libffi
- SQLite (DLLs/sqlite3.dll): public domain. https://www.sqlite.org/copyright.html
- zlib (DLLs/zlib1.dll, when present): zlib license. https://zlib.net/zlib_license.html
- Intel OpenMP runtime (libiomp5md.dll): Intel / LLVM OpenMP license (Apache-2.0 with LLVM exception).
- OpenBLAS (numpy.libs, scipy.libs *openblas*.dll): BSD-3-Clause. https://github.com/OpenMathLib/OpenBLAS
- Microsoft vcomp140 OpenMP runtime (sentencepiece.libs/vcomp140-*.dll, shipped inside the sentencepiece wheel) and
  the msvcp140 copies inside numpy.libs / sentencepiece.libs (shipped inside those wheels): Microsoft Visual C++
  redistributable terms.

Python packages (name version: license as declared in package metadata)
"""


def vc_runtime_src() -> tuple[list[Path], bool]:
    """(folders to take VC_RUNTIME from, True if they are the Visual Studio redist folders).
    TLWU_VCREDIST_DIR (one folder or os.pathsep list), else the Visual Studio redist folders of VCToolsRedistDir
    (set in a VS developer prompt: x64/Microsoft.VC14*.CRT + .OpenMP, the Distributable Code of the VS license),
    else System32 of %SystemRoot% (same files, installed by the Redistributable; the build prints a warning)."""
    if os.environ.get("TLWU_VCREDIST_DIR"):
        return [Path(p) for p in os.environ["TLWU_VCREDIST_DIR"].split(os.pathsep) if p], True
    redist = os.environ.get("VCToolsRedistDir")
    if redist:
        x64 = Path(redist) / "x64"
        dirs = sorted(x64.glob("Microsoft.VC14*.CRT")) + sorted(x64.glob("Microsoft.VC14*.OpenMP"))
        if dirs:
            return dirs, True
    return [Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"], False


def copy_vc_runtime(app: Path, dirs: list[Path] | None = None) -> list[str]:
    """Copy VC_RUNTIME (first match in dirs) into the app folder (on every DLL search path the app uses)."""
    if dirs is None:
        dirs, from_vs = vc_runtime_src()
        if not from_vs:
            print("WARNING: VC++ runtime taken from System32; for a release build run from a Visual Studio developer "
                  "prompt (VCToolsRedistDir) or set TLWU_VCREDIST_DIR to the VS redist CRT and OpenMP folders")
    src = {n: next((d / n for d in dirs if (d / n).is_file()), None) for n in VC_RUNTIME}
    missing = [n for n, f in src.items() if f is None]
    if missing:
        raise SystemExit(f"VC++ runtime: {', '.join(missing)} not found in {', '.join(map(str, dirs))}; install "
                         "the Microsoft Visual C++ Redistributable 2015-2022 (x64) or Build Tools on the pack machine, "
                         "or set TLWU_VCREDIST_DIR")
    found = []
    for n, f in src.items():
        data = f.read_bytes()
        ver = pe_file_version(data)
        if pe_machine(data) != 0x8664:
            raise SystemExit(f"VC++ runtime: {f} is not an x64 DLL (32-bit Python reads SysWOW64?)")
        if ver is None or ver[:2] < VC_MIN_VERSION:
            raise SystemExit(f"VC++ runtime: {f} version {ver} is older than {VC_MIN_VERSION}; update the "
                             "Microsoft Visual C++ Redistributable 2015-2022 (x64) on the pack machine")
        found.append(f"{n} {'.'.join(map(str, ver))}")
    for n, f in src.items():
        shutil.copy2(f, app / n)
    return found


def find_cuda_src(candidates: list[Path] | None = None) -> Path | None:
    """First site-packages that has every nvidia-*-cu12 wheel of CUDA_WHEELS with the DLLs llama.cpp needs.
    #398: the source used to be the project .venv only; a pack from a checkout without it silently shipped
    no cuda_libs/ and a CUDA llama.dll that cannot load."""
    for sp in CUDA_SRC_CANDIDATES if candidates is None else candidates:
        bins = [sp / "nvidia" / w / "bin" for w in CUDA_WHEELS]
        have = {f.name.lower() for b in bins if b.is_dir() for f in b.glob("*.dll")}
        if all(b.is_dir() for b in bins) and all(n.lower() in have for n in LLAMA_CUDA_NEEDS):
            return sp
    return None


def copy_cuda(app: Path, src: Path) -> list[str]:
    dst = app / "cuda_libs"
    dst.mkdir(exist_ok=True)
    notes = []
    for wheel, names in CUDA_WHEELS.items():
        bindir = src / "nvidia" / wheel / "bin"
        for f in sorted(bindir.glob("*.dll")):
            if names is None or f.name in names:
                shutil.copy2(f, dst / f.name)
                notes.append(f"{f.name} ({f.stat().st_size >> 20} MB)")
    missing = [n for n in LLAMA_CUDA_NEEDS if not (dst / n).is_file()]
    if missing:
        raise SystemExit(f"cuda_libs: {', '.join(missing)} not found in {src}")
    return notes


def copy_nvidia_licenses(cuda_src: Path, dst: Path) -> list[Path]:
    """Copy each NVIDIA_LICENSE_WHEELS wheel's licenses/* into dst as NVIDIA-<wheel>-<file>; returns the copies."""
    dst.mkdir(parents=True, exist_ok=True)
    out = []
    for name in NVIDIA_LICENSE_WHEELS:
        for f in sorted(cuda_src.glob(f"nvidia_{name}_cu12-*/licenses/*")):
            if f.is_file():
                out.append(Path(shutil.copy2(f, dst / f"NVIDIA-{name}-{f.name}")))
    return out


def llama_license_file() -> Path | None:
    """llama-cpp-python's LICENSE (MIT, covers the llama.cpp DLLs in the pack) from its installed dist-info."""
    try:
        dist = md.distribution("llama_cpp_python")
    except md.PackageNotFoundError:
        return None
    for f in dist.files or ():
        if f.name.upper().startswith("LICENSE"):
            p = Path(dist.locate_file(f))
            if p.is_file():
                return p
    return None


def make_gpu_pack(app: Path, out: Path, url: str) -> Path:
    """Zip app/cuda_libs/ as the separate GPU pack (#613) and write <out>.manifest.json (name, size, sha256, url).
    app/licenses/* (if any) go under GPU_PACK_LICENSE_DIR/; the DLLs stay at the zip root."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from tinglongwu import gpu_pack

    src = app / "cuda_libs"
    files = sorted(f for f in src.glob("*") if f.is_file()) if src.is_dir() else []
    if not files:
        raise SystemExit(f"gpu pack: no CUDA libraries in {src}")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in files:
            z.write(f, f.name)
        lic = app / GPU_PACK_LICENSE_DIR
        for f in sorted(lic.glob("*")) if lic.is_dir() else ():
            if f.is_file():
                z.write(f, f"{GPU_PACK_LICENSE_DIR}/{f.name}")
    man_path = out.with_name(out.name + ".manifest.json")
    gpu_pack.write_manifest(gpu_pack.make_manifest(out, url), man_path)
    return man_path


# Bump when the pack recipe (build_gpu_pack_only and helpers, tinglongwu/gpu_pack.py) changes what goes into the zip:
# the number is hashed into the key, so the next release rebuilds instead of reusing a stale pack (#765).
GPU_PACK_FORMAT = 1


def gpu_pack_wheels_key(repo: Path | None = None) -> str:
    """sha256 over GPU_PACK_KEY_FILES, the nvrtc version and GPU_PACK_FORMAT: equal key = the pack can be reused (#763)."""
    import hashlib

    h = hashlib.sha256()
    for n in GPU_PACK_KEY_FILES:
        h.update(n.encode() + b"\0" + ((repo or REPO) / n).read_bytes() + b"\0")
    h.update(GPU_PACK_NVRTC.encode())
    h.update(f"\0format={GPU_PACK_FORMAT}".encode())
    return h.hexdigest()


def llama_wheel_version(llama_lib: Path) -> str:
    """llama-cpp-python version of the wheel whose llama_cpp/lib is `llama_lib` (read as text: importing llama_cpp would
    load llama.dll). Exits non-zero when it cannot be read, so a pack never ships without its version (#881)."""
    for name in ("__init__.py", "version.py"):
        f = llama_lib.parent / name
        if f.is_file():
            m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', f.read_text("utf-8", "replace"), re.M)
            if m:
                return m.group(1)
    try:
        return md.version("llama_cpp_python")
    except md.PackageNotFoundError:
        raise SystemExit(f"gpu pack: cannot read the llama-cpp-python version next to {llama_lib}") from None


def build_gpu_pack_only(out_dir: Path, cuda_src: Path | None = None, llama_lib: Path | None = None,
                        url: str = "", repo: Path | None = None, llama_license: Path | None = None,
                        llama_version: str | None = None) -> Path:
    """DIR/tinglongwu-gpu-pack.zip + .manifest.json from the CUDA wheels; never runs Nuitka, needs no tlwu.exe.
    The zip = every file copy_cuda() copies + ggml-cuda.dll; a missing DLL exits non-zero naming it."""
    import tempfile

    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from tinglongwu import gpu_pack

    src = cuda_src or find_cuda_src()
    if src is None:
        raise SystemExit("gpu pack: no CUDA 12 wheels (requirements-gpu.txt) found; set TLWU_CUDA_SRC=<site-packages>")
    if llama_lib is None:
        import importlib.util

        spec = importlib.util.find_spec("llama_cpp")  # not imported: that would load llama.dll
        llama_lib = Path(spec.origin).parent / "lib" if spec and spec.origin else None
    ggml = llama_lib / GPU_PACK_LLAMA_DLL if llama_lib else None
    if ggml is None or not ggml.is_file():
        raise SystemExit(f"gpu pack: {GPU_PACK_LLAMA_DLL} not found in {llama_lib} (install requirements-llama-cuda.txt)")
    llama_version = llama_version or llama_wheel_version(llama_lib)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        copy_cuda(stage, src)  # exits non-zero naming a missing LLAMA_CUDA_NEEDS dll
        # #865: the core zip's llama.dll / ggml.dll are the CPU build and never load ggml-cuda.dll, so the pack
        # ships the whole CUDA build of llama_cpp/lib; device.use_pack_llama_build() points llama_cpp at it.
        for f in sorted(llama_lib.glob("*.dll")):
            shutil.copy2(f, stage / "cuda_libs" / f.name)
        # #764 review A1: the pack redistributes NVIDIA and llama.cpp binaries, so their license texts ship with it
        lic = stage / GPU_PACK_LICENSE_DIR
        if not copy_nvidia_licenses(src, lic):
            raise SystemExit(f"gpu pack: no NVIDIA license files (nvidia_*_cu12-*/licenses/*) found in {src}")
        llama_lic = llama_license or llama_license_file()
        if llama_lic is None or not Path(llama_lic).is_file():
            raise SystemExit("gpu pack: llama-cpp-python LICENSE not found (install requirements-llama-cuda.txt)")
        shutil.copy2(llama_lic, lic / f"LICENSE-llama.cpp-{Path(llama_lic).name}")
        (lic / "THIRD_PARTY_NOTICES.txt").write_text(NVIDIA_NOTE, "utf-8")
        # #881: device.pack_llama_dir() uses the pack's llama build only when this equals llamacheck.LLAMA_VERSION
        (stage / "cuda_libs" / "llama_version.txt").write_text(llama_version + "\n", encoding="utf-8")
        zpath = out_dir / GPU_PACK_ASSET
        man_path = make_gpu_pack(stage, zpath, url)
    man = gpu_pack.load_manifest(man_path)
    man["wheels_key"] = gpu_pack_wheels_key(repo)
    gpu_pack.write_manifest(man, man_path)
    return man_path


def cpu_llama_dlls(cache: Path = LLAMA_CPU, run=subprocess.check_call) -> list[Path]:
    """llama_cpp/lib/*.dll of the CPU wheel of the same llama-cpp-python version (downloaded once into cache).
    TLWU_LLAMA_CPU_WHEEL = a local wheel file to use instead of the download."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from tinglongwu.llamacheck import INDEX, LLAMA_VERSION

    wheel = os.environ.get("TLWU_LLAMA_CPU_WHEEL")
    cache.mkdir(parents=True, exist_ok=True)
    if not wheel:
        pattern = f"llama_cpp_python-{LLAMA_VERSION}-*win_amd64.whl"
        if not list(cache.glob(pattern)):
            run([sys.executable, "-m", "pip", "download", f"llama-cpp-python=={LLAMA_VERSION}", "--no-deps",
                 "--only-binary=:all:", "--index-url", f"{INDEX}/cpu", "-d", str(cache)])
        found = sorted(cache.glob(pattern))
        if not found:
            raise SystemExit(f"no CPU llama-cpp-python {LLAMA_VERSION} wheel in {cache}")
        wheel = str(found[-1])
    out = cache / "lib"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    with zipfile.ZipFile(wheel) as z:
        for name in z.namelist():
            if name.startswith("llama_cpp/lib/") and name.lower().endswith(".dll"):
                (out / Path(name).name).write_bytes(z.read(name))
    dlls = sorted(out.glob("*.dll"))
    if not any(f.name.lower() == "llama.dll" for f in dlls):
        raise SystemExit(f"{wheel} has no llama_cpp/lib/llama.dll")
    return dlls


def copy_llama(app: Path, lite: bool, cpu_dlls: list[Path], src: Path | None = None) -> None:
    """llama_cpp/lib = the pack-venv build (CUDA), llama_cpp/lib_cpu = the CPU build (fallback, #398).
    lite: llama_cpp/lib = the CPU build only (a CUDA ggml.dll imports ggml-cuda.dll, so it cannot run without it)."""
    if src is None:
        import importlib.util

        # find_spec, not import: importing llama_cpp loads llama.dll, which fails without the CUDA DLLs on PATH
        src = Path(importlib.util.find_spec("llama_cpp").origin).parent / "lib"  # from the pack-venv
    dst = app / "llama_cpp" / "lib"
    if lite:
        shutil.rmtree(dst, ignore_errors=True)
        shutil.rmtree(app / "llama_cpp" / "lib_cpu", ignore_errors=True)
        shutil.rmtree(app / "cuda_libs", ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)
    for f in [] if lite else sorted(src.glob("*.dll")):
        shutil.copy2(f, dst / f.name)
    cpu = dst if lite else app / "llama_cpp" / "lib_cpu"
    cpu.mkdir(parents=True, exist_ok=True)
    for f in cpu_dlls:
        shutil.copy2(f, cpu / f.name)


# Packages whose metadata license covers only their code, not the data they ship (#390 review).
DATA_LICENSES = {
    "pymorphy3-dicts-ru": "MIT (code); bundled Russian dictionary data: CC BY-SA 3.0 "
                          "(https://creativecommons.org/licenses/by-sa/3.0/), derived from OpenCorpora "
                          "(opencorpora.org), shipped unmodified",
}


LICENSE_SKIP = ("nuitka", "ordered-set", "zstandard", "pip", "setuptools", "wheel")  # build tools, not shipped


def write_licenses(app: Path, cuda_src: Path | None) -> None:
    lic = app / "licenses"
    lic.mkdir(exist_ok=True)
    rows = []
    for d in sorted(md.distributions(), key=lambda x: (x.metadata["Name"] or "").lower()):
        n = d.metadata["Name"]
        if n and n.lower() not in LICENSE_SKIP:
            lic_text = DATA_LICENSES.get(n.lower()) or d.metadata.get('License-Expression') or d.metadata.get('License')
            rows.append(f"{n} {d.version}: {lic_text or 'see package'}")
    if cuda_src:
        copy_nvidia_licenses(cuda_src, lic)
    import importlib.util

    spec = importlib.util.find_spec("soundfile")
    # soundfile is a single module: site-packages/soundfile.py beside site-packages/_soundfile_data/
    cand = Path(spec.origin).parent / "_soundfile_data" / "COPYING" if spec and spec.origin else None
    if not (cand and cand.exists()):
        cand = REPO / ".venv" / "Lib" / "site-packages" / "_soundfile_data" / "COPYING"
    if cand and cand.exists():
        shutil.copy2(cand, lic / "LICENSE-libsndfile-LGPL-2.1.txt")
    head = NOTICES_HEAD if cuda_src else NOTICES_HEAD.replace(NVIDIA_NOTE, "")
    (lic / "THIRD_PARTY_NOTICES.txt").write_text(head + "\n".join(rows) + "\n", "utf-8")


def make_zip(app: Path, out: Path) -> None:
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(app.rglob("*")):
            if f.is_file() and not (f.parent == app and f.suffix.lower() == ".whl"):  # no wheel at the top (#948)
                z.write(f, Path(app.name) / f.relative_to(app))


# Source files the TTS worker (run by the Kokoro venv's python, not by tlwu.exe) needs next to the exe (#464):
# TTSClient starts tinglongwu/reverse/tts_worker.py, which imports tinglongwu.offline for the network guard.
# The app layer ships every .py of the app packages, so these are checked, not copied one by one.
WORKER_SOURCES = ("tinglongwu/__init__.py", "tinglongwu/offline.py", "tinglongwu/i18n.py", "tinglongwu/userdirs.py",
                  "tinglongwu/reverse/tts_worker.py")  # i18n/userdirs: offline._emit (blocked-connection log)
APP_PACKAGES = ("tinglongwu", "tlwu_app", "tinglongwu_setup")  # = runtime_layer.APP_PACKAGES (tested)
# Where each app package lives in the repo (src/ layer layout, #1664). tinglongwu is split over four layer dirs and
# merged into one tinglongwu/ in the app layer (core first; its __init__ skips layer dirs that do not exist).
APP_PACKAGE_SOURCES = {
    "tinglongwu": ("src/core/tinglongwu", "src/ui/tinglongwu", "src/flow/tinglongwu", "src/system/tinglongwu"),
    "tlwu_app": ("src/ui/tlwu_app",),
    "tinglongwu_setup": ("src/system/tinglongwu_setup",),
}
APP_MANIFEST = "app-layer.json"  # at the top of the folder: app version of the app layer
USAGE_TEXT = "使用說明.txt"
EXE_NAME = "tlwu.exe"
VS_FIXEDFILEINFO_SIG = b"\xbd\x04\xef\xfe"  # 0xFEEF04BD, little endian
VERSION_OFFSET = 8  # dwFileVersionMS in VS_FIXEDFILEINFO; LS, dwProductVersionMS, LS follow
VERSION_FIELDS = "<IIII"  # those four DWORDs
VERSION_PART_MAX = 0xFFFF  # each of the four version parts is one 16-bit word


def _runtime_layer():
    """release/packaging/runtime_layer.py as a module (this file is run as a script and imported by path in tests)."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import runtime_layer

    return runtime_layer


def set_exe_version(exe: Path, version: str) -> bool:
    """Write `version` ('0.3.5.0') as file and product version into the exe's version resource, in place.
    The launcher is compiled once per runtime layer with version 0.0.0.0; the app layer stamps the real one, so a
    version bump does not rebuild the runtime layer. False = the exe has no single VS_FIXEDFILEINFO (left alone)."""
    data = bytearray(exe.read_bytes())
    i = data.find(VS_FIXEDFILEINFO_SIG)
    end = i + VERSION_OFFSET + struct.calcsize(VERSION_FIELDS)
    if i < 0 or data.find(VS_FIXEDFILEINFO_SIG, i + 1) >= 0 or len(data) < end:
        return False
    parts = [int(x) for x in version.split(".")]
    if len(parts) != 4 or not all(0 <= p <= VERSION_PART_MAX for p in parts):
        raise SystemExit(f"{exe.name}: version {version!r} is not four numbers of 0..{VERSION_PART_MAX}")
    a, b, c, d = parts
    ms, ls = (a << 16) | b, (c << 16) | d
    struct.pack_into(VERSION_FIELDS, data, i + VERSION_OFFSET, ms, ls, ms, ls)
    exe.write_bytes(bytes(data))
    return True


def variants(lite: bool, allow_cpu_only: bool) -> list[bool]:
    """Runtime variants (True = CPU-only) this command line accepts, preferred first."""
    return [True] if lite else ([False, True] if allow_cpu_only else [False])


def runtime_stale(layer: Path, lite: bool, allow_cpu_only: bool) -> str:
    """'' when the runtime layer in `layer` can be reused for this command line, else the reason."""
    rt = _runtime_layer()
    man = rt.read_manifest(layer)
    for v in variants(lite, allow_cpu_only):
        if man.get("lite") is v:
            return rt.stale_reason(layer, v)
    return rt.stale_reason(layer, lite) or "the runtime layer is the other variant (CPU-only vs CUDA)"


def build_runtime(layer: Path, lite: bool, allow_cpu_only: bool) -> dict:
    """Build the runtime layer into `layer` from this interpreter (a packaging venv on Windows). Returns its manifest."""
    rt = _runtime_layer()
    if sys.platform != "win32":
        raise SystemExit("the runtime layer is built on Windows only (it copies the running Python's DLLs)")
    cuda_src = None if lite else find_cuda_src()
    if cuda_src is None and not lite:
        msg = ("no CUDA 12 DLLs (nvidia-cublas/cuda-runtime/cudnn/cuda-nvrtc -cu12 wheels) in "
               f"{', '.join(map(str, CUDA_SRC_CANDIDATES))} (#398). For the GPU package: pip install -r "
               "requirements-gpu.txt into the pack-venv, or set TLWU_CUDA_SRC=<site-packages>")
        if not allow_cpu_only:
            raise SystemExit(f"{msg}; or pass --lite / --allow-cpu-only for a CPU-only package")
        print(f"WARNING: {msg}; building a CPU-only package (--allow-cpu-only)")
    new = layer.with_name(layer.name + ".new")  # built beside the old one, swapped in when complete
    shutil.rmtree(new, ignore_errors=True)
    new.mkdir(parents=True)
    print("python:", rt.copy_python_core(new))
    print("packages:", " ".join(rt.copy_site_packages(new)), "; stubs:", rt.copy_stubs(new))
    copy_llama(new, lite=cuda_src is None, cpu_dlls=cpu_llama_dlls())
    print("cuda:", copy_cuda(new, cuda_src) if cuda_src else "none (CPU-only package)")
    print("vc runtime:", copy_vc_runtime(new))
    write_licenses(new, cuda_src)
    print("compiled (.py -> .pyc), kept as .py:", rt.compile_sourceless(new))
    tool = rt.build_launcher(new / EXE_NAME, BUILD / "launcher")
    if pe_machine((new / EXE_NAME).read_bytes()) != 0x8664:
        raise SystemExit(f"runtime layer: {tool} did not build an x64 {EXE_NAME}")
    print("launcher:", tool)
    gui_tool = rt.build_launcher(new / rt.GUI_EXE_NAME, BUILD / "launcher_gui", gui=True)  # #1524
    for exe, want in ((EXE_NAME, PE_SUBSYSTEM_WINDOWS_CUI), (rt.GUI_EXE_NAME, PE_SUBSYSTEM_WINDOWS_GUI)):
        got = pe_subsystem((new / exe).read_bytes())
        if got != want:
            raise SystemExit(f"runtime layer: {exe} has PE subsystem {got}, expected {want} (#1524: the shortcut "
                             f"target must be a GUI exe, tlwu.exe a console exe)")
    print("gui launcher:", gui_tool)
    if tool != "msvc" and not rt.copy_mingw_copying(tool, new):
        print(f"WARNING: no MinGW-w64 COPYING file found for {tool}; licenses/{rt.MINGW_COPYING_NAME} not shipped")
    man = rt.write_manifest(new, cuda_src is None, tool)
    shutil.rmtree(layer, ignore_errors=True)
    new.rename(layer)
    return man


def ensure_runtime(layer: Path, lite: bool, allow_cpu_only: bool, reuse_only: bool = False, force: bool = False,
                   run=subprocess.check_call) -> dict:
    """The runtime layer's manifest; the layer is reused when up to date, else rebuilt (in a new packaging venv when
    this interpreter is not one: --lite only)."""
    rt = _runtime_layer()
    why = "--rebuild-runtime" if force else runtime_stale(layer, lite, allow_cpu_only)
    if not why:
        print(f"runtime layer: reused ({layer})")
        return rt.read_manifest(layer)
    if reuse_only:
        raise SystemExit(f"--post-only: {why}; run without --post-only first")
    print(f"runtime layer: building ({why})")
    if rt.has_app_deps():
        return build_runtime(layer, lite, allow_cpu_only)
    if not lite:
        raise SystemExit("the CUDA variant needs a packaging venv (requirements.txt without av / psutil + "
                         "requirements-llama-cuda.txt): start this script with its python, or pass --lite")
    py = rt.make_pack_venv(PACK_VENV)
    run([str(py), str(Path(__file__).resolve()), "--runtime-only", "--rebuild-runtime", "--lite"])
    return rt.read_manifest(layer)


def link_tree(src: Path, dst: Path, copy: tuple[str, ...] = (EXE_NAME,)) -> int:
    """dst = a copy of src made of hard links (same volume; a plain copy where linking fails). Top-level files named
    in `copy` are real copies: the app layer changes them in place (set_exe_version). Returns the file count."""
    if dst.exists():
        shutil.rmtree(dst)
    n = 0
    for root, _dirs, files in os.walk(src):
        out = dst / Path(root).relative_to(src)
        out.mkdir(parents=True, exist_ok=True)
        for name in files:
            s, d = Path(root) / name, out / name
            if out == dst and name in copy:
                shutil.copy2(s, d)
            else:
                try:
                    os.link(s, d)
                except OSError:
                    shutil.copy2(s, d)
            n += 1
    return n


def build_app_layer(app: Path, pyc_magic: str | None = None, repo: Path | None = None) -> list[str]:
    """Put the app layer into `app`: the app packages as .py + unchecked-hash .pyc (never recompiled on the user's
    PC, whatever the file times are), the usage text and APP_MANIFEST. `pyc_magic` = the runtime layer's; this
    interpreter must write the same .pyc format. Shipping the sources alone is not an option: an install made over
    an older one keeps that version's unchecked-hash .pyc files, and Python would run them instead of the new .py."""
    rt = _runtime_layer()
    repo = repo or REPO
    if pyc_magic not in (None, rt.pyc_magic()):
        raise SystemExit(f"app layer: this Python ({sys.version.split()[0]}) writes another .pyc format than the "
                         "runtime layer's Python; rebuild the runtime layer (--rebuild-runtime)")
    for pkg in APP_PACKAGES:
        dst = app / pkg
        if dst.exists():
            shutil.rmtree(dst)
        for src in APP_PACKAGE_SOURCES[pkg]:
            if (repo / src).is_dir():
                shutil.copytree(repo / src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                                dirs_exist_ok=True)
        if not compileall.compile_dir(
                str(dst), quiet=1, invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH):
            raise SystemExit(f"app layer: {pkg} has a file that does not compile")
    missing = [rel for rel in WORKER_SOURCES if not (app / rel).is_file()]
    if missing:
        raise SystemExit(f"app layer: TTS worker sources missing: {', '.join(missing)} (#464)")
    shutil.copy2(repo / "release" / "packaging" / USAGE_TEXT, app / USAGE_TEXT)
    (app / APP_MANIFEST).write_text(json.dumps({"version": read_version(), "packages": list(APP_PACKAGES)}) + "\n",
                                    "utf-8")
    return list(APP_PACKAGES)


APP_LAYER_ASSET = "tinglongwu-app-layer.zip"  # = scripts/release_check.py APP_LAYER_ASSET (#945)


def make_app_layer_zip(app: Path, out_dir: Path) -> Path:
    """Zip the app layer of `app` (a built dist/tlwu) as out_dir/APP_LAYER_ASSET and write <zip>.manifest.json
    {name, version, size, sha256, runtime_key, lite} (#945). Files sit at the zip root (no top folder), so the zip
    unpacks over an install folder. Only what build_app_layer wrote goes in: the APP_PACKAGES folders, APP_MANIFEST,
    USAGE_TEXT and the version-stamped tlwu.exe; the runtime layer (Python, DLLs, packages) never does.
    runtime_key = the `key` of the runtime layer this folder was built on (its runtime-layer.json)."""
    rt = _runtime_layer()
    try:
        info = json.loads((app / APP_MANIFEST).read_text("utf-8"))
        runtime = rt.read_manifest(app)
        version = info["version"]
        key = runtime["key"]
    except (OSError, ValueError, KeyError) as e:
        raise SystemExit(f"app layer zip: {app} lacks a readable {APP_MANIFEST} / {rt.MANIFEST_NAME}: {e}") from e
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / APP_LAYER_ASSET
    files = [app / USAGE_TEXT, app / APP_MANIFEST, app / EXE_NAME]
    for pkg in APP_PACKAGES:
        files += [f for f in (app / pkg).rglob("*") if f.is_file()]
    absent = [f.name for f in files if not f.is_file()]
    if absent:
        raise SystemExit(f"app layer zip: missing {', '.join(absent)} in {app}")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(files):
            z.write(f, f.relative_to(app).as_posix())
    h = hashlib.sha256()
    with open(out, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    man = {"name": APP_LAYER_ASSET, "version": version, "size": out.stat().st_size, "sha256": h.hexdigest(),
           "runtime_key": key, "lite": bool(runtime.get("lite"))}
    man_path = out.with_name(out.name + ".manifest.json")
    man_path.write_text(json.dumps(man, indent=1) + "\n", "utf-8")
    return man_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--post-only", action="store_true",
                    help="never rebuild the runtime layer: stop when it is missing or out of date")
    ap.add_argument("--rebuild-runtime", action="store_true", help="rebuild the runtime layer even when it is up to date")
    ap.add_argument("--runtime-only", action="store_true", help="only make sure the runtime layer is built, no app layer / zip")
    ap.add_argument("--lite", "--core", dest="lite", action="store_true",
                    help="CPU-only core variant (alias --core): no cuda_libs/ and no ggml-cuda.dll")
    ap.add_argument("--allow-cpu-only", action="store_true",
                    help="no CUDA wheels found: build a CPU-only package instead of stopping; on a machine "
                         "without an NVIDIA driver (CI runner) the smoke may also use the CPU llama fallback")
    ap.add_argument("--no-smoke", action="store_true", help="skip the `tlwu.exe --smoke` check before zipping")
    ap.add_argument("--gpu-pack-only", action="store_true",
                    help="only build the GPU pack (zip + manifest) from the CUDA wheels, no core build (#763)")
    ap.add_argument("--out", help="with --gpu-pack-only: output folder")
    ap.add_argument("--url", default="", help="with --gpu-pack-only: download url written to the manifest")
    a = ap.parse_args()
    if a.gpu_pack_only:
        if not a.out:
            raise SystemExit("--gpu-pack-only needs --out DIR")
        print("manifest:", build_gpu_pack_only(Path(a.out), url=a.url))
        return 0
    t0 = time.monotonic()
    man = ensure_runtime(RUNTIME, a.lite, a.allow_cpu_only, reuse_only=a.post_only, force=a.rebuild_runtime)
    t1 = time.monotonic()
    print(f"runtime layer: {t1 - t0:.0f} s (key {str(man.get('key'))[:12]}, python {man.get('python')})")
    if a.runtime_only:
        return 0
    lite = bool(man.get("lite"))
    DIST.mkdir(parents=True, exist_ok=True)
    print("core folder:", link_tree(RUNTIME, APP), "runtime files")
    print("app layer:", build_app_layer(APP, man.get("pyc_magic")))
    if not set_exe_version(APP / EXE_NAME, WIN_VERSION):
        print(f"WARNING: {EXE_NAME} has no version resource; its file version stays unset")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(REPO / "scripts"))
    from check_dist_dlls import check as check_dlls
    from clean_machine_check import cmd_deps

    if check_dlls(APP):  # a DLL dependent that does not resolve even on this machine: it cannot load anywhere (#398)
        raise SystemExit("check_dist_dlls: unresolved DLL dependents (MISSING lines above), not zipping")

    if cmd_deps(APP):  # a DLL that only the build machine has: tlwu.exe would not start on a clean Windows (#9)
        print("WARNING: see the MISSING lines above; then test with: clean_machine_check.py sandbox", APP, "--run")
    if not a.no_smoke:
        smoke(APP, lite=lite, no_gpu_ok=a.allow_cpu_only and not has_nvidia_driver())
    t2 = time.monotonic()
    make_zip(APP, DIST / "tinglongwu-win64.zip")
    print("zip:", DIST / "tinglongwu-win64.zip", (DIST / "tinglongwu-win64.zip").stat().st_size >> 20, "MB")
    print("app layer zip:", make_app_layer_zip(APP, DIST))
    print(f"app layer + checks: {t2 - t1:.0f} s, zip: {time.monotonic() - t2:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
