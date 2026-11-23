"""#1774: every CPU fallback on the Vulkan path prints exactly one line: the reason and a copy-paste fix.

Pure text helpers (no I/O); mt_backend / asr_vulkan / engines / cli call them where a Vulkan backend was asked
for (or half-installed) and the CPU is used instead. The fix command is `tlwu-setup ...` everywhere except the
frozen Windows app, where the same entry point is `tlwu.exe setup ...` (tlwu_app.main routes it).
"""
from __future__ import annotations

import sys

KINDS = ("no_pack", "pack_version", "init_failed", "missing_model", "no_loader")
_LOADER_NAME = {"win32": "vulkan-1.dll", "darwin": "libvulkan.1.dylib"}


def setup_cmd(platform: str | None = None) -> str:
    return "tlwu.exe setup" if (platform or sys.platform) == "win32" else "tlwu-setup"


def _one_line(text) -> str:
    return " ".join(str(text).split())


def fallback_line(kind: str, *, use: str = "MT", platform: str | None = None, have: str = "", want: str = "",
                  error: str = "", size: str = "small") -> str:
    """One line "tlwu: <reason>; using CPU for <use>. Fix: <command>". `use` is "MT" or "ASR"."""
    if kind not in KINDS:
        raise ValueError(f"unknown Vulkan fallback kind: {kind!r}")
    plat = platform or sys.platform
    cmd = setup_cmd(plat)
    pack = f"{cmd} --vulkan-pack"
    if kind == "no_pack":
        why, fix = "Vulkan pack is not installed", pack
    elif kind == "pack_version":
        why = f"Vulkan pack is for version {have or 'unknown'}, app is {want or 'unknown'}"
        fix = pack
    elif kind == "init_failed":
        why = f"Vulkan init failed ({_one_line(error) or 'unknown error'})"
        fix = f"update the GPU driver, then {pack}; TLWU_DEVICE=cpu silences this"
    elif kind == "missing_model":
        why = f"whisper.cpp model ggml-{size}.bin is not downloaded"
        fix = f"{cmd} --whisper-cpp {size}"
    else:
        lib = _LOADER_NAME.get(plat, "libvulkan.so.1")
        why = f"Vulkan loader {lib} not found"
        fix = ("install your GPU's Vulkan driver (Linux: sudo apt install libvulkan1 mesa-vulkan-drivers)"
               if plat.startswith("linux") else "install or update your GPU driver")
    return f"tlwu: {why}; using CPU for {use}. Fix: {fix}"
