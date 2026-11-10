"""Hardware probe + tier recommendation (#63 part 1). Local only, no extra dependencies, no network.

`probe()` reads GPU name + total VRAM (nvidia-smi), physical RAM and CPU cores. `recommend()` maps that to
the speech/translation models to use and lists the ones that cannot fit, with a reason, so the user never has
to pick "simple / deluxe" by hand. The quick speed benchmark and runtime downgrade are separate steps.

Footprints (MiB, model loaded + working memory):
- MT, GPU: Qwen3-8B 5148 / Qwen3-4B 3062 (README benchmark table, peak VRAM, RTX 3050 8 GB);
  Hy-MT2-1.8B ~1500 (#63 measurements, home RTX 3050).
- MT, CPU: Hy-MT2-1.8B RAM peak ~1900 (#63 owner CPU test, i5-13500).
- ASR, GPU: large-v3 fp16 ~4000, medium ~2500 (README); large-v3-turbo has 4 decoder layers instead of 32,
  fp16 weights ~1.6 GB -> 1600 as a conservative upper bound (int8_float16 is smaller).
Owner decision (#63, 2026-11-10): main target is a 4-8 GB GPU with Hy-MT2; CPU keeps OPUS by default.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field

GPU_MIN_VRAM_MIB = 4096  # owner: "GPU threshold ~4 GB" (turbo 1600 + Hy-MT2 1500 + headroom)
HEADROOM_MIB = 512  # CUDA context, display, other apps

ASR_VRAM_MIB = {"large-v3-turbo": 1600, "large-v3": 4000, "medium": 2500}
MT_VRAM_MIB = {"hymt2": 1500, "qwen3-4b": 3062, "qwen3-8b": 5148}
HYMT_CPU_RAM_MIB = 1900
CPU_MIN_RAM_FOR_HYMT_MIB = 4096  # Hy-MT2 peak 1.9 GB + Whisper small + the OS


@dataclass(frozen=True)
class HwInfo:
    gpu: str | None  # NVIDIA GPU name, None if no usable nvidia-smi
    vram_mib: int | None  # total VRAM of GPU 0
    ram_mib: int | None  # physical RAM
    cores: int  # logical CPU cores
    driver: str | None = None  # NVIDIA driver version ("457.49"), None if unknown


@dataclass
class Tier:
    name: str  # "gpu" | "cpu"
    device: str  # "cuda" | "cpu"
    asr: str  # Whisper model name
    mt: str  # "hymt2" | "opus"
    reason: str
    excluded: dict[str, str] = field(default_factory=dict)  # model -> why it cannot be used here


def gpu_info(run=subprocess.run) -> tuple[str | None, int | None]:
    """(name, total MiB) of GPU 0 via nvidia-smi; (None, None) when there is no NVIDIA driver."""
    try:
        out = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                  capture_output=True, text=True, timeout=10).stdout
        name, total = out.strip().splitlines()[0].rsplit(",", 1)
        return name.strip(), int(total.strip())
    except Exception:  # noqa: BLE001 - no driver, no nvidia-smi, unexpected output
        return None, None


def ram_mib() -> int | None:
    """Physical RAM in MiB (POSIX sysconf, Windows GlobalMemoryStatusEx); None if unknown."""
    if sys.platform == "win32":
        try:
            import ctypes

            class MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = MemStatus()
            st.dwLength = ctypes.sizeof(MemStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return int(st.ullTotalPhys // (1024 * 1024))
        except Exception:  # noqa: BLE001
            return None
        return None
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024))
    except (ValueError, OSError, AttributeError):
        return None


def probe(gpu=gpu_info, ram=ram_mib, cores=os.cpu_count, driver=None) -> HwInfo:
    name, vram = gpu()
    if driver is None:
        from .device import driver_version as driver
    return HwInfo(gpu=name, vram_mib=vram, ram_mib=ram(), cores=cores() or 1,
                  driver=driver() if name else None)


def recommend(hw: HwInfo) -> Tier:
    """Pick the tier for this machine; every model that cannot fit is listed in `excluded` with the reason."""
    excluded: dict[str, str] = {}
    vram = hw.vram_mib
    if vram is not None:
        budget = vram - HEADROOM_MIB
        for asr, need in ASR_VRAM_MIB.items():
            if need > budget:
                excluded[f"whisper {asr} (GPU)"] = f"needs ~{need} MiB VRAM, {vram} MiB total"
        for mt, need in MT_VRAM_MIB.items():
            if ASR_VRAM_MIB["large-v3-turbo"] + need > budget:
                excluded[f"{mt} (GPU)"] = (f"needs ~{need} MiB VRAM next to Whisper turbo "
                                          f"({ASR_VRAM_MIB['large-v3-turbo']} MiB), {vram} MiB total")
    from .device import driver_too_old, gpu_problem

    old_driver = driver_too_old(hw.driver)  # GPU is there but CUDA 12 cannot use it (#89): CPU tier + how to fix
    if vram is not None and vram >= GPU_MIN_VRAM_MIB and not old_driver:
        return Tier("gpu", "cuda", "large-v3-turbo", "hymt2",
                    f"{hw.gpu} with {vram} MiB VRAM (>= {GPU_MIN_VRAM_MIB} MiB)", excluded)
    if vram is None:
        why = "no NVIDIA GPU found"
    elif old_driver:
        why = f"{hw.gpu}: {gpu_problem(hw.driver)}"
    else:
        why = f"{hw.gpu} has {vram} MiB VRAM (< {GPU_MIN_VRAM_MIB} MiB)"
    if hw.ram_mib is not None and hw.ram_mib < CPU_MIN_RAM_FOR_HYMT_MIB:
        excluded["hymt2 (CPU)"] = f"needs ~{HYMT_CPU_RAM_MIB} MiB RAM, {hw.ram_mib} MiB total"
    return Tier("cpu", "cpu", "small", "opus", why + (" CPU mode until then" if old_driver else ": CPU mode"),
                excluded)


def describe(hw: HwInfo, tier: Tier) -> str:
    gb = (lambda m: "unknown" if m is None else f"{m / 1024:.1f} GB")
    lines = [f"GPU: {hw.gpu or 'none'} ({gb(hw.vram_mib)} VRAM)" if hw.gpu else "GPU: none (no NVIDIA driver)",
             f"RAM: {gb(hw.ram_mib)}, CPU cores: {hw.cores}",
             f"Recommended: {tier.name.upper()} tier -> speech model {tier.asr}, translation {tier.mt} "
             f"({tier.reason})"]
    lines += [f"  not used: {m} - {why}" for m, why in tier.excluded.items()]
    return "\n".join(lines)
