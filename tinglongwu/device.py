"""Compute-device selection: --device auto|cpu|cuda with a safe CPU fallback.

CTranslate2 (4.x) on CUDA needs cuBLAS 12 and cuDNN 9 at run time. If they are missing the
failure only shows up on the first inference, so we probe the libraries up front.
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
from pathlib import Path

CHOICES = ("auto", "cpu", "cuda")
_LIBS = {"win32": ("cublas64_12.dll", "cudnn64_9.dll"),
         "linux": ("libcublas.so.12", "libcudnn.so.9")}


def _nvidia_wheel_dirs() -> list[Path]:
    """bin/ (Windows) or lib/ (Linux) dirs of the pip wheels nvidia-cublas-cu12 / nvidia-cudnn-cu12."""
    try:
        import nvidia  # namespace package shared by the nvidia-*-cu12 wheels
    except ImportError:
        return []
    out = []
    for root in list(getattr(nvidia, "__path__", [])):
        for sub in sorted(Path(root).glob("*")):
            for name in ("bin", "lib"):
                if (sub / name).is_dir():
                    out.append(sub / name)
    return out


def _bundled_dirs() -> list[Path]:
    """`cuda_libs/` next to the standalone build (tlwu.exe folder) that holds cuBLAS / cuDNN / cudart."""
    d = Path(__file__).resolve().parent.parent / "cuda_libs"
    return [d] if d.is_dir() else []


def register_cuda_libs() -> None:
    """Make DLLs from the bundled cuda_libs/ or pip-installed nvidia-* wheels findable (no CUDA toolkit needed)."""
    for d in _bundled_dirs() + _nvidia_wheel_dirs():
        if sys.platform == "win32":
            os.add_dll_directory(str(d))
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
        elif str(d) not in os.environ.get("LD_LIBRARY_PATH", ""):
            # dlopen reads LD_LIBRARY_PATH only at start-up, so preload by absolute path instead
            for lib in d.glob("*.so*"):
                try:
                    ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


# What the CUDA build of llama.cpp (ggml-cuda.dll) imports besides the bundle itself: CUDA runtime + driver (#398)
LLAMA_CUDA_DLLS = ("cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll", "nvcuda.dll")


def bundled_llama_cpu_dir() -> Path | None:
    """`llama_cpp/lib_cpu/` of the standalone build: a CPU-only llama.cpp next to the CUDA one in llama_cpp/lib/."""
    d = Path(__file__).resolve().parent.parent / "llama_cpp" / "lib_cpu"
    return d if (d / "llama.dll").is_file() else None


llama_cpu_reason: str | None = None  # why the CUDA llama build was not used (set when the CPU build is selected)


def use_llama_cpu_build(load=ctypes.CDLL) -> bool:
    """Point llama-cpp-python at the bundled CPU build (LLAMA_CPP_LIB_PATH) when the CUDA one cannot load here:
    no NVIDIA driver (nvcuda.dll) or CUDA runtime DLLs missing (#398). Call before `import llama_cpp`.
    True when the CPU build is selected. Without a bundled CPU build (pip installs) nothing changes."""
    cpu = bundled_llama_cpu_dir()
    if cpu is None or sys.platform != "win32":
        return False
    if os.environ.get("LLAMA_CPP_LIB_PATH"):
        return Path(os.environ["LLAMA_CPP_LIB_PATH"]) == cpu
    global llama_cpu_reason
    register_cuda_libs()
    for name in LLAMA_CUDA_DLLS:
        try:
            load(name)
        except OSError as e:
            llama_cpu_reason = f"{name}: {e}"
            break
    else:
        return False
    os.environ["LLAMA_CPP_LIB_PATH"] = str(cpu)
    return True


def missing_cuda_libs() -> list[str]:
    register_cuda_libs()
    missing = []
    for name in _LIBS.get(sys.platform if sys.platform in _LIBS else "linux", ()):
        try:
            ctypes.CDLL(name)
        except OSError:
            missing.append(name)
    return missing


def cuda_device_count() -> int:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count()
    except Exception:  # noqa: BLE001 - any failure means "no usable CUDA"
        return 0


CUDA_HELP = ("install the CUDA 12 runtime libraries once (with network): "
             "`tlwu-setup --gpu`  (= pip install nvidia-cublas-cu12 nvidia-cudnn-cu12), "
             "plus an NVIDIA driver supporting CUDA 12+")
if os.environ.get("TLWU_BUNDLED") == "1":  # standalone build ships the libs; only the driver can be the problem
    CUDA_HELP = "update your NVIDIA graphics driver (CUDA 12 or newer) to use the GPU"


# Oldest NVIDIA driver that can run CUDA 12.x (CTranslate2 4.x, cuBLAS 12, cuDNN 9). Source: NVIDIA "CUDA
# Toolkit and Corresponding Driver Versions" (CUDA release notes, table "minimum required driver version for
# CUDA minor version compatibility"): CUDA 12.x -> Linux >= 525.60.13, Windows >= 528.33.
MIN_DRIVER = {"win32": (528, 33), "linux": (525, 60, 13)}
DRIVER_URL = "https://www.nvidia.com/Download/index.aspx"


def driver_version(run=subprocess.run) -> str | None:
    """NVIDIA driver version of GPU 0 as nvidia-smi prints it ('457.49'); None: no NVIDIA GPU / no driver."""
    try:
        out = run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"], capture_output=True,
                  text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:  # noqa: BLE001 - no nvidia-smi, broken driver
        return None
    line = (out or "").strip().splitlines()[0].strip() if (out or "").strip() else ""
    return line if re.fullmatch(r"\d+(\.\d+)+", line) else None


def min_driver(platform: str | None = None) -> tuple[int, ...]:
    return MIN_DRIVER["win32" if (platform or sys.platform) == "win32" else "linux"]


def driver_too_old(version: str | None, platform: str | None = None) -> bool:
    """True when an NVIDIA driver is installed but predates CUDA 12 (the GPU is there, CUDA cannot use it)."""
    if not version:
        return False
    return tuple(int(x) for x in version.split(".")) < min_driver(platform)


def gpu_problem(version: str | None, platform: str | None = None) -> str | None:
    """Why CTranslate2 sees no CUDA device although nvidia-smi sees a GPU, in the UI language, with what to do.
    None when there is no NVIDIA GPU at all (nothing to fix)."""
    if not version:
        return None
    from .i18n import t

    if driver_too_old(version, platform):
        return t("gpu_driver_old", have=version, need=".".join(map(str, min_driver(platform))), url=DRIVER_URL)
    return t("gpu_no_cuda", have=version)


def resolve_device(choice: str = "auto", *, count=None, missing=None,
                   warn=lambda m: print(m, file=sys.stderr), driver=None) -> str:
    """Return 'cuda' or 'cpu'. auto: cuda if CTranslate2 sees a device and the libs load. A GPU that is present
    but unusable (driver too old for CUDA 12) is never a silent CPU fallback: the real reason is warned."""
    if choice not in CHOICES:
        raise ValueError(f"device must be one of {CHOICES}")
    if choice == "cpu":
        return "cpu"
    count, missing, driver = count or cuda_device_count, missing or missing_cuda_libs, driver or driver_version
    if count() == 0:
        why = gpu_problem(driver())
        if why:
            warn(f"tlwu: {why}")
        elif choice == "cuda":
            warn("tlwu: --device cuda requested but CTranslate2 reports no CUDA device; using CPU.")
        return "cpu"
    miss = missing()
    if miss:
        warn(f"tlwu: CUDA device found but {', '.join(miss)} missing; falling back to CPU. To use the GPU, "
             + CUDA_HELP + ".")
        return "cpu"
    return "cuda"


def compute_type(device: str, role: str = "asr") -> str:
    """CPU: int8. GPU: float16 for ASR, int8_float16 for MT models."""
    if device == "cuda":
        return "float16" if role == "asr" else "int8_float16"
    return "int8"


def cpu_threads(count=None, env=None) -> int:
    """Whisper threads for the CPU speak path (#186): half the logical CPUs (~ physical cores), 4..8.
    faster-whisper's default is 4. Env TLWU_CPU_THREADS overrides."""
    import os

    raw = (os.environ if env is None else env).get("TLWU_CPU_THREADS")
    if raw and raw.isdigit() and int(raw) > 0:
        return int(raw)
    n = count if count is not None else (os.cpu_count() or 8)
    return max(4, min(8, n // 2))


ASR_FP16_MIN_FREE_MIB = 9000  # below this much free VRAM at load time Whisper uses int8_float16 (see asr_compute_type)


def asr_compute_type(device: str, free_mib=None) -> str:
    """Whisper compute type. float16 normally; on a tight GPU (8 GB card that must also hold the MT model) fp16
    Whisper made every pass ~4x slower (1.7 s vs 0.35 s per 30 s window, measured on an RTX 3050 8 GB with
    large-v3-turbo + a 7B-8B LLM: the card runs at 7.6/8.2 GB and spills). int8_float16 halves the weights with
    negligible WER change. Env TLWU_ASR_COMPUTE overrides."""
    import os

    forced = os.environ.get("TLWU_ASR_COMPUTE")
    if forced:
        return forced
    if device != "cuda":
        return "int8"
    if free_mib is None:
        from .engines import free_vram_mib

        free_mib = free_vram_mib()
    return "int8_float16" if free_mib is not None and free_mib < ASR_FP16_MIN_FREE_MIB else "float16"
