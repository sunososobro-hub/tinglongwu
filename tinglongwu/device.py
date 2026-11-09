"""Compute-device selection: --device auto|cpu|cuda with a safe CPU fallback.

CTranslate2 (4.x) on CUDA needs cuBLAS 12 and cuDNN 9 at run time. If they are missing the
failure only shows up on the first inference, so we probe the libraries up front.
"""
from __future__ import annotations

import ctypes
import os
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


def resolve_device(choice: str = "auto", *, count=cuda_device_count, missing=missing_cuda_libs,
                   warn=lambda m: print(m, file=sys.stderr)) -> str:
    """Return 'cuda' or 'cpu'. auto: cuda if CTranslate2 sees a device and the libs load."""
    if choice not in CHOICES:
        raise ValueError(f"device must be one of {CHOICES}")
    if choice == "cpu":
        return "cpu"
    if count() == 0:
        if choice == "cuda":
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


ASR_FP16_MIN_FREE_MIB = 9000  # below this much free VRAM at load time Whisper uses int8_float16 (see asr_compute_type)


def asr_compute_type(device: str, free_mib=None) -> str:
    """Whisper compute type. float16 normally; on a tight GPU (8 GB card that must also hold Qwen3-8B) fp16
    Whisper made every pass ~4x slower (1.7 s vs 0.35 s per 30 s window, measured on an RTX 3050 8 GB with
    large-v3-turbo + Qwen3-8B: the card runs at 7.6/8.2 GB and spills). int8_float16 halves the weights with
    negligible WER change. Env TLWU_ASR_COMPUTE overrides."""
    import os

    forced = os.environ.get("TLWU_ASR_COMPUTE")
    if forced:
        return forced
    if device != "cuda":
        return "int8"
    if free_mib is None:
        from .qwen import free_vram_mib

        free_mib = free_vram_mib()
    return "int8_float16" if free_mib is not None and free_mib < ASR_FP16_MIN_FREE_MIB else "float16"
