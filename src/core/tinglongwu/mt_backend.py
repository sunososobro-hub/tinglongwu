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
from .paths import APP_ROOT
from .vulkan_hint import fallback_line

BACKENDS = ("cuda", "vulkan", "cpu")
VULKAN_PACK_NAME = "vulkan-pack"
VERSION_FILE = "version.txt"  # written into the pack by the installer (#797); must equal the app's __version__
last_backend = ""  # backend the last MT model load chose (selftest reports it)
last_threads = 0  # llama.cpp decode threads of the last MT model load (#1198: GUI path line)
_LOADER = {"win32": "vulkan-1.dll", "darwin": "libvulkan.1.dylib"}


def vulkan_pack_dir(base: Path | None = None, warn=None) -> Path | None:
    """Installed Vulkan llama.cpp pack folder (has a llama library), else None. Env TLWU_VULKAN_PACK moves it.
    A pack whose version.txt is missing or differs from the app version is stale (#909): None, one warning line."""
    d = base or (Path(os.environ["TLWU_VULKAN_PACK"]) if os.environ.get("TLWU_VULKAN_PACK")
                 else APP_ROOT / VULKAN_PACK_NAME)
    try:
        if d.is_dir() and (any(d.glob("llama.*")) or any(d.glob("libllama.*"))):
            from . import __version__

            try:
                have = (d / VERSION_FILE).read_text("utf-8").strip()
            except OSError:
                have = ""
            if have != __version__:
                (warn or print)(fallback_line("pack_version", have=have, want=__version__))
                return None
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


def forced_vulkan_unavailable(pack, env=None, present=None) -> str:
    """#1774: TLWU_DEVICE=vulkan was asked for but cannot run: "no_pack" | "no_loader" (hint kind), else "".
    Auto mode stays quiet (a machine without a Vulkan pack gets no line on every start). A stale pack already
    printed its own pack_version line in vulkan_pack_dir()."""
    if ((os.environ if env is None else env).get("TLWU_DEVICE") or "").strip().lower() != "vulkan":
        return ""
    if pack is None:
        return "" if (APP_ROOT / VULKAN_PACK_NAME).is_dir() or os.environ.get("TLWU_VULKAN_PACK") else "no_pack"
    return "" if (present or vulkan_device_present)() else "no_loader"


def vulkan_available(pack=None, present=None) -> bool:
    return (pack or vulkan_pack_dir)() is not None and (present or vulkan_device_present)()


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
        warn(fallback_line("init_failed", error=f"{type(e).__name__}: {e}"))
        return loader(path, "cpu"), "cpu"


def path_of(llm) -> tuple[str, int] | None:
    """#1198: (backend, decode threads) a loaded MT model runs on, from shared_llama's tags; None when unknown."""
    backend = getattr(llm, "backend", "")
    return (backend, int(getattr(llm, "threads", 0) or 0)) if backend in BACKENDS else None
