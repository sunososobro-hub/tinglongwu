"""MT (llama.cpp) compute backend: CUDA > Vulkan > CPU (#746).

The Vulkan build of llama.cpp runs on AMD / Intel / NVIDIA GPUs without CUDA. It ships as an optional pack
(`vulkan-pack/`, same mechanism as the GPU pack #613). Env TLWU_DEVICE=vulkan forces the Vulkan path even when CUDA
is present (test it on any NVIDIA PC or Intel iGPU laptop); TLWU_DEVICE=cpu|cuda force those. A Vulkan init
failure never crashes: the caller falls back to the CPU (`load_with_fallback`).
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

BACKENDS = ("cuda", "vulkan", "cpu")
VULKAN_PACK_NAME = "vulkan-pack"
last_backend = ""  # backend the last MT model load chose (selftest reports it)
_LOADER = {"win32": "vulkan-1.dll", "darwin": "libvulkan.1.dylib"}


def vulkan_pack_dir(base: Path | None = None) -> Path | None:
    """Installed Vulkan llama.cpp pack folder (has a llama library), else None. Env TLWU_VULKAN_PACK moves it."""
    d = base or (Path(os.environ["TLWU_VULKAN_PACK"]) if os.environ.get("TLWU_VULKAN_PACK")
                 else Path(__file__).resolve().parent.parent / VULKAN_PACK_NAME)
    try:
        if d.is_dir() and (any(d.glob("llama.*")) or any(d.glob("libllama.*"))):
            return d.resolve()
    except OSError:
        pass
    return None


def vulkan_device_present(load=ctypes.CDLL) -> bool:
    """The Vulkan loader library is installed (GPU drivers ship it)."""
    try:
        load(_LOADER.get(sys.platform, "libvulkan.so.1"))
        return True
    except OSError:
        return False


def vulkan_available(pack=vulkan_pack_dir, present=vulkan_device_present) -> bool:
    return pack() is not None and present()


def choose_backend(*, cuda: bool, vulkan: bool, env=None) -> str:
    """cuda > vulkan > cpu. TLWU_DEVICE=vulkan|cuda|cpu forces that backend when it is usable, else the
    normal order applies (a forced backend that is not there is never an error, just the next one)."""
    forced = ((os.environ if env is None else env).get("TLWU_DEVICE") or "").strip().lower()
    usable = {"cuda": cuda, "vulkan": vulkan, "cpu": True}
    if forced in usable and usable[forced]:
        return forced
    return next(b for b in BACKENDS if usable[b])


def load_with_fallback(path, backend: str, loader, warn=lambda m: print(m, file=sys.stderr)):
    """`loader(path, backend)`; a Vulkan init failure falls back to the CPU with one warning, never a crash.
    Returns (model, backend actually used)."""
    if backend != "vulkan":
        return loader(path, backend), backend
    try:
        return loader(path, "vulkan"), "vulkan"
    except Exception as e:  # noqa: BLE001 - driver / ICD / out of memory
        warn(f"tlwu: Vulkan init failed ({type(e).__name__}: {e}); using CPU for MT.")
        return loader(path, "cpu"), "cpu"
