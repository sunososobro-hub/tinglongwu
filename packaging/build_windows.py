"""Build the standalone Windows folder app (tlwu.exe) with Nuitka and zip it.

Run with the PACKAGING venv (never the project .venv):
    C:\\Users\\user\\tlwu-eval\\pack-venv\\Scripts\\python.exe packaging\\build_windows.py [--post-only] [--lite]

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
VC_RUNTIME = ["vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
              "concrt140.dll"]


def nuitka_cmd() -> list[str]:
    return [sys.executable, "-m", "nuitka", "--standalone", "--assume-yes-for-downloads", "--jobs=16", "--lto=no",
            "--enable-plugin=tk-inter", "--windows-console-mode=hide", "--output-filename=tlwu.exe",
            "--output-folder-name=tlwu", f"--output-dir={BUILD}", "--remove-output",
            f"--report={EVAL / 'build' / 'nuitka-report.xml'}",
            "--include-package=tlwu_app", "--include-package=tinglongwu", "--include-package=tinglongwu_setup",
            "--nofollow-import-to=tinglongwu.reverse", "--nofollow-import-to=pytest",
            "--nofollow-import-to=unittest",
            "--include-package-data=faster_whisper", "--include-package-data=opencc",
            "--include-package-data=soundcard",
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


def copy_vc_runtime(app: Path) -> list[str]:
    got = []
    sysdir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    for n in VC_RUNTIME:
        if not (app / n).exists() and (sysdir / n).exists():
            shutil.copy2(sysdir / n, app / n)
            got.append(n)
    return got


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
    (lic / "THIRD_PARTY_NOTICES.txt").write_text(
        "tinglongWu bundles the following Python packages (name version: license as declared in package metadata).\n"
        "Models are NOT bundled; they are downloaded on first run (Whisper: MIT, OPUS-MT/Argos: CC-BY-4.0/MIT, "
        "Qwen3: Apache-2.0).\nNVIDIA CUDA runtime DLLs in cuda_libs/ are redistributed under the NVIDIA CUDA Toolkit "
        "EULA (cudart, cuBLAS, NVRTC) and the cuDNN Software License Agreement; see the NVIDIA-*.txt files.\n"
        "Microsoft Visual C++ runtime DLLs (vcruntime140*, msvcp140*) are redistributed under the Visual Studio "
        "redistributable terms (app-local deployment).\nNo GPL components: PyAV/FFmpeg is NOT included.\n\n"
        + "\n".join(rows) + "\n", "utf-8")


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
    if not (APP / "tlwu.exe").exists():
        raise SystemExit("run without --post-only first")
    copy_llama(APP)
    print("vc runtime:", copy_vc_runtime(APP))
    print("cuda:", copy_cuda(APP))
    write_licenses(APP)
    shutil.copy2(REPO / "packaging" / "使用說明.txt", APP / "使用說明.txt")
    make_zip(APP, DIST / "tinglongwu-win64.zip")
    print("zip:", DIST / "tinglongwu-win64.zip", (DIST / "tinglongwu-win64.zip").stat().st_size >> 20, "MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
