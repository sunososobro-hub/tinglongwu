r"""Build the standalone Windows folder app (tlwu.exe) with Nuitka and zip it.

Run with the PACKAGING venv (never the project .venv):
    %USERPROFILE%\tlwu-eval\pack-venv\Scripts\python.exe packaging\build_windows.py [--post-only] [--lite]

pack-venv = requirements.txt (without av / psutil) + requirements-llama-cuda.txt (llama-cpp-python CUDA wheel) + Nuitka.
PyAV is replaced by packaging/stubs/av because its wheel bundles GPL FFmpeg codecs (libx264/libx265).
CUDA runtime DLLs are copied from the nvidia-*-cu12 wheels (requirements-gpu.txt), found in TLWU_CUDA_SRC, the
pack-venv or the project .venv (read only). None found -> the build stops (--lite / --allow-cpu-only: CPU-only); never a
package whose CUDA llama.dll cannot load (#398). A CPU llama.cpp build (the llama-cpp-python CPU wheel, downloaded
once) goes to llama_cpp/lib_cpu/: the app uses it when the CUDA llama.dll cannot load (no NVIDIA GPU / driver).
Steps: nuitka (needs MinGW-w64 gcc, which Nuitka downloads once) -> dist/tlwu -> cuda_libs/, llama DLLs,
VC++ runtime, licenses, usage text -> scripts/check_dist_dlls.py (every DLL dependent resolves, #398) ->
`tlwu.exe --smoke` (#310; --no-smoke skips) -> dist/tinglongwu-win64.zip.
"""
from __future__ import annotations

import argparse
import importlib.metadata as md
import os
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EVAL = Path(os.environ.get("TLWU_BUILD_ROOT", str(Path.home() / "tlwu-eval")))
BUILD = EVAL / "build" / "nuitka-tlwu"


def read_version() -> str:
    """tinglongwu.__version__ is the single source of truth (read as text: no import, so no offline guard side effects)."""
    import re

    m = re.search(r'^__version__\s*=\s*"([^"]+)"', (REPO / "tinglongwu" / "__init__.py").read_text("utf-8"), re.M)
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
# Microsoft VC++ 2015-2022 runtime, absent on a clean Windows: ctranslate2 / llama.cpp import msvcp140, onnxruntime
# msvcp140_1, ggml vcomp140 (OpenMP). Copied as one matched set from the installed redistributable (System32) next
# to tlwu.exe, replacing the older vcruntime140 Nuitka took from Python: msvcp140 must not be newer than vcruntime140.
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


def lazy_includes() -> list[str]:
    """--include-module for every submodule filelock loads through its module __getattr__ (#310: filelock._soft was
    missing from a user's build). --include-package=filelock should already cover them; naming them makes Nuitka
    fail loudly if one cannot be found in the pack-venv instead of shipping a build that breaks on first download."""
    import filelock

    lazy = getattr(filelock, "_LAZY_ATTRIBUTES", {})
    mods = {f"filelock{m}" if m.startswith(".") else m for m in lazy.values()}
    return [f"--include-module={m}" for m in sorted(mods)]


def smoke(app: Path, run=subprocess.run, lite: bool = False) -> None:
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
        raise SystemExit("tlwu.exe --smoke: the CUDA llama build did not load on the pack machine (CPU line above), "
                         "not zipping")


def nuitka_cmd() -> list[str]:
    return [sys.executable, "-m", "nuitka", "--standalone", "--assume-yes-for-downloads", "--jobs=16", "--lto=no",
            "--enable-plugin=tk-inter", "--windows-console-mode=hide", "--output-filename=tlwu.exe",
            "--output-folder-name=tlwu", f"--output-dir={BUILD}", "--remove-output",
            f"--report={EVAL / 'build' / 'nuitka-report.xml'}",
            "--include-package=tlwu_app", "--include-package=tinglongwu", "--include-package=tinglongwu_setup",
            # filelock/huggingface_hub import submodules lazily (__getattr__), so Nuitka cannot see them (#36)
            "--include-package=filelock", "--include-package=huggingface_hub", *lazy_includes(),
            # tinglongwu.reverse is compiled in: tlwu --voice (#330) uses its Player / TTSClient (#464). tts_worker is
            # not: it runs in the Kokoro venv from the source file copied by copy_worker_sources().
            "--nofollow-import-to=tinglongwu.reverse.tts_worker", "--nofollow-import-to=pytest",
            "--nofollow-import-to=unittest",
            "--include-package-data=faster_whisper", "--include-package-data=opencc",
            "--include-package-data=soundcard", "--include-package=sv_ttk", "--include-package-data=sv_ttk", "--include-package-data=tinglongwu_setup", "--include-package-data=tinglongwu",  # self-test clip data/selftest_en.wav (#99)
            "--product-name=tinglongWu", "--file-description=tinglongWu offline speech translator",
            f"--file-version={WIN_VERSION}", f"--product-version={WIN_VERSION}", str(REPO / "packaging" / "tlwu_entry.py")]


def run_nuitka() -> Path:
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(REPO), str(REPO / "packaging" / "stubs")]))
    subprocess.check_call(nuitka_cmd(), cwd=REPO, env=env)
    out = BUILD / "tlwu"
    if not (out / "tlwu.exe").exists():
        out = BUILD / "tlwu.dist"
    if not (out / "tlwu.exe").exists():
        raise SystemExit(f"tlwu.exe not found under {BUILD}")
    return out


NVIDIA_NOTE = """This package includes NVIDIA CUDA and cuDNN libraries (cuda_libs/) redistributed under the NVIDIA CUDA Toolkit EULA
and the cuDNN Software License Agreement; see the NVIDIA-*.txt files in this folder.
"""
NOTICES_HEAD = """tinglongWu itself is MIT licensed: https://github.com/sunososobro-hub/tinglongwu

Models are NOT bundled; they are downloaded on first run (Whisper: MIT, Opus-MT: CC-BY-4.0, Argos Translate: MIT,
Silero VAD: MIT, Qwen3: Apache-2.0).
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
- Python runtime (python312.dll): Python Software Foundation License. https://docs.python.org/3/license.html
- Tcl/Tk (tcl86t.dll, tk86t.dll): Tcl/Tk license (BSD-style). https://www.tcl.tk/software/tcltk/license.html
- OpenSSL (libcrypto-3.dll): Apache-2.0. https://www.openssl.org/
- SQLite (sqlite3.dll): public domain. https://www.sqlite.org/copyright.html
- zlib (zlib1.dll): zlib license. https://zlib.net/zlib_license.html
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


def write_licenses(app: Path, cuda_src: Path | None) -> None:
    lic = app / "licenses"
    lic.mkdir(exist_ok=True)
    rows = []
    for d in sorted(md.distributions(), key=lambda x: (x.metadata["Name"] or "").lower()):
        n = d.metadata["Name"]
        if n and n.lower() not in ("nuitka", "ordered-set", "zstandard", "pip", "setuptools", "wheel"):
            rows.append(f"{n} {d.version}: {d.metadata.get('License-Expression') or d.metadata.get('License') or 'see package'}")
    for name in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc") if cuda_src else ():
        for f in cuda_src.glob(f"nvidia_{name}_cu12-*/licenses/*"):
            shutil.copy2(f, lic / f"NVIDIA-{name}-{f.name}")
    import importlib.util

    spec = importlib.util.find_spec("soundfile")
    cand = Path(spec.origin).parent.parent / "_soundfile_data" / "COPYING" if spec and spec.origin else None
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
            if f.is_file():
                z.write(f, Path(app.name) / f.relative_to(app))


# Source files the TTS worker (run by the Kokoro venv's python, not by tlwu.exe) needs next to the exe (#464):
# TTSClient starts tinglongwu/reverse/tts_worker.py, which imports tinglongwu.offline for the network guard.
WORKER_SOURCES = ("tinglongwu/__init__.py", "tinglongwu/offline.py", "tinglongwu/i18n.py", "tinglongwu/userdirs.py",
                  "tinglongwu/reverse/tts_worker.py")  # i18n/userdirs: offline._emit (blocked-connection log)


def copy_worker_sources(app: Path) -> list[str]:
    for rel in WORKER_SOURCES:
        dst = app / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, dst)
    return list(WORKER_SOURCES)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--post-only", action="store_true", help="skip nuitka, reuse the build output")
    ap.add_argument("--lite", action="store_true", help="CPU-only variant: no CUDA libs / ggml-cuda.dll")
    ap.add_argument("--allow-cpu-only", action="store_true",
                    help="no CUDA wheels found: build a CPU-only package instead of stopping")
    ap.add_argument("--no-smoke", action="store_true", help="skip the `tlwu.exe --smoke` check before zipping")
    a = ap.parse_args()
    DIST.mkdir(parents=True, exist_ok=True)
    if not a.post_only:
        src = run_nuitka()
        if APP.exists():
            shutil.rmtree(APP)
        shutil.copytree(src, APP)
    elif not APP.exists():
        built = BUILD / "tlwu.dist"
        if not built.exists():
            raise SystemExit(f"nothing to post-process: {built} missing; run without --post-only first")
        shutil.copytree(built, APP)
    if not (APP / "tlwu.exe").exists():
        raise SystemExit("run without --post-only first")
    cuda_src = None if a.lite else find_cuda_src()
    if cuda_src is None and not a.lite:
        msg = ("no CUDA 12 DLLs (nvidia-cublas/cuda-runtime/cudnn/cuda-nvrtc -cu12 wheels) in "
               f"{', '.join(map(str, CUDA_SRC_CANDIDATES))} (#398). For the GPU package: pip install -r "
               "requirements-gpu.txt into the pack-venv, or set TLWU_CUDA_SRC=<site-packages>")
        if not a.allow_cpu_only:
            raise SystemExit(f"{msg}; or pass --lite / --allow-cpu-only for a CPU-only package")
        print(f"WARNING: {msg}; building a CPU-only package (--allow-cpu-only)")
    copy_llama(APP, lite=cuda_src is None, cpu_dlls=cpu_llama_dlls())
    print("cuda:", copy_cuda(APP, cuda_src) if cuda_src else "none (CPU-only package)")
    print("vc runtime:", copy_vc_runtime(APP))
    write_licenses(APP, cuda_src)
    shutil.copy2(REPO / "packaging" / "使用說明.txt", APP / "使用說明.txt")
    print("worker sources:", copy_worker_sources(APP))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(REPO / "scripts"))
    from check_dist_dlls import check as check_dlls
    from clean_machine_check import cmd_deps

    if check_dlls(APP):  # a DLL dependent that does not resolve even on this machine: it cannot load anywhere (#398)
        raise SystemExit("check_dist_dlls: unresolved DLL dependents (MISSING lines above), not zipping")

    if cmd_deps(APP):  # a DLL that only the build machine has: tlwu.exe would not start on a clean Windows (#9)
        print("WARNING: see the MISSING lines above; then test with: clean_machine_check.py sandbox", APP, "--run")
    if not a.no_smoke:
        smoke(APP, lite=cuda_src is None)
    make_zip(APP, DIST / "tinglongwu-win64.zip")
    print("zip:", DIST / "tinglongwu-win64.zip", (DIST / "tinglongwu-win64.zip").stat().st_size >> 20, "MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
