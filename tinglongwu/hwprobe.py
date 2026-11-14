"""Hardware probe + tier recommendation (#63 part 1). Local only, no extra dependencies, no network.

`probe()` reads GPU name + total VRAM (nvidia-smi), physical RAM and CPU cores. `recommend()` maps that to
the speech/translation models to use and lists the ones that cannot fit, with a reason, so the user never has
to pick "simple / deluxe" by hand. The quick speed benchmark and runtime downgrade are separate steps.

Footprints (MiB, model loaded + working memory):
- MT, GPU: Hy-MT2-1.8B ~1500 (#63 measurements, home RTX 3050).
- MT, CPU: Hy-MT2-1.8B RAM peak ~1900 (#63 owner CPU test, i5-13500).
- ASR, GPU: large-v3 fp16 ~4000, medium ~2500 (README); large-v3-turbo has 4 decoder layers instead of 32,
  fp16 weights ~1.6 GB -> 1600 as a conservative upper bound (int8_float16 is smaller).
Owner decision (#63, 2026-11-10): main target is a 4-8 GB GPU with Hy-MT2; CPU uses Hy-MT2 on CPU (OPUS was removed, #471).
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field

GPU_MIN_VRAM_MIB = 4096  # owner: "GPU threshold ~4 GB" (turbo 1600 + Hy-MT2 1500 + headroom)
HEADROOM_MIB = 512  # CUDA context, display, other apps

ASR_VRAM_MIB = {"large-v3-turbo": 1600, "large-v3": 4000, "medium": 2500}
MT_VRAM_MIB = {"hymt2": 1500}
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
    mt: str  # "hymt2" | "hymt2-cpu"
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
    return Tier("cpu", "cpu", "small", "hymt2-cpu", why + (" CPU mode until then" if old_driver else ": CPU mode"),
                excluded)


QUALITY_LEVELS = ("auto", "fast", "balanced", "quality")
# Speech model per level (#81). balanced = the recommended tier. Source: #63 WER table (en/ja/ru: small
# 7.0/11.8/14.0, turbo 5.8/5.1/9.3) -> turbo is the accuracy step above small on any hardware; fast is one size
# below the recommended model (GPU small, CPU base); GPU quality is large-v3 when it fits next to Hy-MT2.
FAST_ASR = {"gpu": "small", "cpu": "base"}


def preset(level: str, hw: HwInfo) -> Tier:
    """Tier for a Fast / Balanced / Quality choice on this machine; auto and balanced = recommend(hw).
    Translation stays the tier's engine (GPU Hy-MT2, CPU Hy-MT2 - owner rule #63); only the speech model moves."""
    tier = recommend(hw)
    if level == "fast":
        tier.asr = FAST_ASR[tier.name]
        tier.reason += "; fast: smaller speech model, lowest delay"
    elif level == "quality":
        if tier.name == "cpu":
            tier.asr = "large-v3-turbo"
            tier.reason += "; quality: larger speech model, slower on a CPU"
        elif (hw.vram_mib or 0) >= ASR_VRAM_MIB["large-v3"] + MT_VRAM_MIB[tier.mt] + HEADROOM_MIB:
            tier.asr = "large-v3"
            tier.reason += "; quality: largest speech model"
        else:
            tier.reason += "; quality: large-v3 does not fit in this VRAM next to the translation model"
    elif level not in ("auto", "balanced"):
        raise ValueError(f"unknown quality level {level!r} (use one of {', '.join(QUALITY_LEVELS)})")
    return tier


def describe(hw: HwInfo, tier: Tier) -> str:
    gb = (lambda m: "unknown" if m is None else f"{m / 1024:.1f} GB")
    lines = [f"GPU: {hw.gpu or 'none'} ({gb(hw.vram_mib)} VRAM)" if hw.gpu else "GPU: none (no NVIDIA driver)",
             f"RAM: {gb(hw.ram_mib)}, CPU cores: {hw.cores}",
             f"Recommended: {tier.name.upper()} tier -> speech model {tier.asr}, translation {tier.mt} "
             f"({tier.reason})"]
    lines += [f"  not used: {m} - {why}" for m, why in tier.excluded.items()]
    return "\n".join(lines)


# #780 / #745 part 1: CPU-mode auto settings. Pure function, no probing. Values are conservative defaults chosen from
# the existing footprints (hwprobe: Hy-MT2 CPU ~1.9 GB + Whisper small + OS) and the #207 / #250 thread notes; the
# measured latency/chrF/WER table is #781 (part 2) and may retune them.
CPU_HEAVY_RAM_GB = 24  # below this `medium` and `large-v3` are marked "too heavy for this PC" in the picker
HEAVY_MODELS = ("medium", "large-v3")


@dataclass(frozen=True)
class CpuMode:
    size: str     # Whisper model size
    beam: int     # beam size (1 = greedy, fastest)
    threads: int  # ASR cpu_threads
    merge: bool   # merge short fragments before translating (fewer, longer MT calls)


def cpu_mode_settings(cores: int, ram_gb: float) -> CpuMode:
    """Speed-first ASR settings for a PC with no CUDA device. Threads leave one core for the MT engine and the GUI."""
    cores = max(1, int(cores))
    threads = max(1, min(cores - 1, 8)) if cores > 1 else 1
    if ram_gb < 8 or cores < 4:
        return CpuMode("base", 1, threads, True)
    if ram_gb < 16 or cores < 8:
        return CpuMode("small", 1, threads, True)
    return CpuMode("small", 2, threads, False)


def too_heavy(model: str, ram_gb: float | None) -> bool:
    """True when `model` is a heavy Whisper size and this PC has less than CPU_HEAVY_RAM_GB (unknown RAM: not marked)."""
    return ram_gb is not None and model in HEAVY_MODELS and ram_gb < CPU_HEAVY_RAM_GB
