r"""Build the standalone Windows folder app (tlwu.exe) with Nuitka and zip it.

Run with the PACKAGING venv (never the project .venv):
    %USERPROFILE%\tlwu-eval\pack-venv\Scripts\python.exe packaging\build_windows.py [--post-only] [--lite]

pack-venv = requirements.txt (without av / psutil) + requirements-qwen.txt (llama-cpp-python CUDA wheel) + Nuitka.
PyAV is replaced by packaging/stubs/av because its wheel bundles GPL FFmpeg codecs (libx264/libx265).
CUDA runtime DLLs are copied from the nvidia-*-cu12 wheels of CUDA_SRC (default: the project .venv, read only).
Steps: nuitka (needs MinGW-w64 gcc, which Nuitka downloads once) -> dist/tlwu -> cuda_libs/, llama DLLs,
VC++ runtime, licenses, usage text -> dist/tinglongwu-win64.zip.
"""
from __future__ import annotations

import argparse
import importlib.metadata as md
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EVAL = Path(os.environ.get("TLWU_BUILD_ROOT", str(Path.home() / "tlwu-eval")))
BUILD = EVAL / "build" / "nuitka-tlwu"
DIST = EVAL / "dist"
APP = DIST / "tlwu"
CUDA_SRC = REPO / ".venv" / "Lib" / "site-packages"

# cuBLAS/cuDNN for CTranslate2 (Whisper) and cudart/cuBLAS for llama.cpp (ggml-cuda.dll)
CUDA_WHEELS = {"cublas": ["cublas64_12.dll", "cublasLt64_12.dll"],  # nvblas64_12.dll is not needed
               "cuda_runtime": ["cudart64_12.dll"],
               "cuda_nvrtc": ["nvrtc64_120_0.dll", "nvrtc-builtins64_129.dll"],  # cuDNN runtime-compiled engines
               "cudnn": None}  # None = all DLLs
LITE_SKIP = ("cuda_libs", "llama_cpp/lib/ggml-cuda.dll")  # CPU-only variant


def nuitka_cmd() -> list[str]:
    return [sys.executable, "-m", "nuitka", "--standalone", "--assume-yes-for-downloads", "--jobs=16", "--lto=no",
            "--enable-plugin=tk-inter", "--windows-console-mode=hide", "--output-filename=tlwu.exe",
            "--output-folder-name=tlwu", f"--output-dir={BUILD}", "--remove-output",
            f"--report={EVAL / 'build' / 'nuitka-report.xml'}",
            "--include-package=tlwu_app", "--include-package=tinglongwu", "--include-package=tinglongwu_setup",
            # filelock/huggingface_hub import submodules lazily (__getattr__), so Nuitka cannot see them (#36)
            "--include-package=filelock", "--include-package=huggingface_hub",
            "--nofollow-import-to=tinglongwu.reverse", "--nofollow-import-to=pytest",
            "--nofollow-import-to=unittest",
            "--include-package-data=faster_whisper", "--include-package-data=opencc",
            "--include-package-data=soundcard", "--include-package-data=tinglongwu_setup",
            "--product-name=tinglongWu", "--file-description=tinglongWu offline speech translator",
            "--file-version=0.1.0.0", "--product-version=0.1.0.0", str(REPO / "packaging" / "tlwu_entry.py")]


def run_nuitka() -> Path:
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(REPO), str(REPO / "packaging" / "stubs")]))
    subprocess.check_call(nuitka_cmd(), cwd=REPO, env=env)
    out = BUILD / "tlwu"
    if not (out / "tlwu.exe").exists():
        out = BUILD / "tlwu.dist"
    if not (out / "tlwu.exe").exists():
        raise SystemExit(f"tlwu.exe not found under {BUILD}")
    return out


NOTICES_HEAD = """tinglongWu itself is MIT licensed: https://github.com/sunososobro-hub/tinglongwu

Models are NOT bundled; they are downloaded on first run (Whisper: MIT, Opus-MT: CC-BY-4.0, Argos Translate: MIT,
Silero VAD: MIT, Qwen3: Apache-2.0).
Microsoft Visual C++ runtime is NOT bundled by us: install the Microsoft Visual C++ Redistributable 2015-2022 (x64).
This package includes NVIDIA CUDA and cuDNN libraries (cuda_libs/) redistributed under the NVIDIA CUDA Toolkit EULA
and the cuDNN Software License Agreement; see the NVIDIA-*.txt files in this folder.
No GPL components: PyAV/FFmpeg is NOT included (stubbed out).

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


def copy_cuda(app: Path) -> list[str]:
    dst = app / "cuda_libs"
    dst.mkdir(exist_ok=True)
    notes = []
    for wheel, names in CUDA_WHEELS.items():
        bindir = CUDA_SRC / "nvidia" / wheel / "bin"
        for f in sorted(bindir.glob("*.dll")):
            if names is None or f.name in names:
                shutil.copy2(f, dst / f.name)
                notes.append(f"{f.name} ({f.stat().st_size >> 20} MB)")
    return notes


def copy_llama(app: Path) -> None:
    import importlib.util

    # find_spec, not import: importing llama_cpp loads llama.dll, which fails without the CUDA DLLs on PATH
    src = Path(importlib.util.find_spec("llama_cpp").origin).parent / "lib"  # from the pack-venv
    dst = app / "llama_cpp" / "lib"
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.glob("*.dll"):
        if not (dst / f.name).exists():
            shutil.copy2(f, dst / f.name)


def write_licenses(app: Path) -> None:
    lic = app / "licenses"
    lic.mkdir(exist_ok=True)
    rows = []
    for d in sorted(md.distributions(), key=lambda x: (x.metadata["Name"] or "").lower()):
        n = d.metadata["Name"]
        if n and n.lower() not in ("nuitka", "ordered-set", "zstandard", "pip", "setuptools", "wheel"):
            rows.append(f"{n} {d.version}: {d.metadata.get('License-Expression') or d.metadata.get('License') or 'see package'}")
    for name in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
        for f in (CUDA_SRC).glob(f"nvidia_{name}_cu12-*/licenses/*"):
            shutil.copy2(f, lic / f"NVIDIA-{name}-{f.name}")
    import importlib.util

    spec = importlib.util.find_spec("soundfile")
    cand = Path(spec.origin).parent.parent / "_soundfile_data" / "COPYING" if spec and spec.origin else None
    if not (cand and cand.exists()):
        cand = REPO / ".venv" / "Lib" / "site-packages" / "_soundfile_data" / "COPYING"
    if cand and cand.exists():
        shutil.copy2(cand, lic / "LICENSE-libsndfile-LGPL-2.1.txt")
    (lic / "THIRD_PARTY_NOTICES.txt").write_text(NOTICES_HEAD + "\n".join(rows) + "\n", "utf-8")


def make_zip(app: Path, out: Path) -> None:
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(app.rglob("*")):
            if f.is_file():
                z.write(f, Path(app.name) / f.relative_to(app))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--post-only", action="store_true", help="skip nuitka, reuse the build output")
    ap.add_argument("--lite", action="store_true", help="CPU-only variant: no CUDA libs / ggml-cuda.dll")
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
    copy_llama(APP)
    print("cuda:", copy_cuda(APP))
    write_licenses(APP)
    shutil.copy2(REPO / "packaging" / "使用說明.txt", APP / "使用說明.txt")
    make_zip(APP, DIST / "tinglongwu-win64.zip")
    print("zip:", DIST / "tinglongwu-win64.zip", (DIST / "tinglongwu-win64.zip").stat().st_size >> 20, "MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
