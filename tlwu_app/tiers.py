"""GPU detection, model tiers and download planning for the first-run setup. Pure logic, no tkinter, no network.

The tiers mirror install.ps1 (chosen by TOTAL VRAM): the runtime `--engine auto` re-checks free VRAM at start-up.
GPU tier = tinglongwu.hwprobe.GPU_MIN_VRAM_MIB (owner #63: main target is a 4-8 GB GPU with Hy-MT2; #66: Hy-MT2 is
as accurate as Qwen3-8B and ~4x faster). Qwen was removed (#276, #283, #308).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MB = 1024 * 1024
WHISPER_PREFERENCE = ("large-v3-turbo", "large-v3", "medium", "small", "base", "tiny")

# approximate download sizes in bytes (from the pinned repos), used for the progress bar only
SIZES = {"whisper-small": 464 * MB, "whisper-large-v3-turbo": 1640 * MB, "opus": 200 * MB,
         "hymt2-1.8b": 1130 * MB}
HYMT_DIR, HYMT_FILE = "hymt2-1.8b", "Hy-MT2-1.8B-Q4_K_M.gguf"  # == tinglongwu.hymt (what tlwu loads)
GPU_MIN_TOTAL_MB = 4096  # == tinglongwu.hwprobe.GPU_MIN_VRAM_MIB (turbo 1600 + Hy-MT2 1500 + headroom)


@dataclass(frozen=True)
class Tier:
    name: str            # gpu | gpu-min | cpu
    whisper: str
    mt: tuple[str, ...]  # extra downloader flags for the translation model: ("--hymt",) | ()
    label_zh: str
    parts: tuple[str, ...]  # keys of SIZES

    @property
    def total_bytes(self) -> int:
        return sum(SIZES[p] for p in self.parts)

    @property
    def size_text(self) -> str:
        gb = self.total_bytes / (1024 * MB)
        return f"約 {gb:.1f} GB" if gb >= 1 else f"約 {self.total_bytes // MB} MB"

    def downloader_args(self) -> list[str]:
        # #178: ja/ru packs on demand; #254: zh-en has no committed pin yet, the GUI setup is the consent
        return ["--whisper", self.whisper, "--packs", "zh,en", "--allow-unpinned", *self.mt]


def _tier(name, whisper, mt, label, mparts):
    return Tier(name, whisper, mt, label, (f"whisper-{whisper}", "opus", *mparts))


T_GPU = _tier("gpu", "large-v3-turbo", ("--hymt",), "GPU：最準確（Whisper large-v3-turbo + Hy-MT2 翻譯）", ("hymt2-1.8b",))
# #276/#301: Hy-MT2 is the only default engine, so every tier downloads it (below 4 GB VRAM / no GPU it runs on the CPU)
T_MIN = _tier("gpu-min", "small", ("--hymt",), "精簡（Whisper small + Hy-MT2 翻譯在 CPU 執行，顯示卡記憶體不足 4 GB）",
              ("hymt2-1.8b",))
T_CPU = _tier("cpu", "small", ("--hymt",), "CPU 模式（Whisper small + Hy-MT2 翻譯，較慢但不需顯示卡）", ("hymt2-1.8b",))


@dataclass(frozen=True)
class Gpu:
    name: str
    total_mb: int
    free_mb: int
    driver: str = ""  # nvidia-smi driver_version ('457.49'); '' when unknown


SMI_ARGS = ["--query-gpu=name,memory.total,memory.free,driver_version", "--format=csv,noheader,nounits"]


def parse_smi(text: str) -> Gpu | None:
    """First line of `nvidia-smi --query-gpu=name,memory.total,memory.free[,driver_version] --format=csv,noheader,nounits`."""
    for line in (text or "").splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) >= 3:
            try:
                total, free = int(f[1]), int(f[2])
            except ValueError:
                return None
            driver = f[3] if len(f) >= 4 and re.fullmatch(r"\d+(\.\d+)+", f[3]) else ""
            return Gpu(f[0], total, free, driver)
    return None


def _nvidia_smi() -> str | None:
    p = shutil.which("nvidia-smi")
    if p:
        return p
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("SystemRoot", r"C:\Windows")):
        for sub in (r"NVIDIA Corporation\NVSMI\nvidia-smi.exe", r"System32\nvidia-smi.exe"):
            c = Path(base) / sub
            if c.exists():
                return str(c)
    return None


def probe_gpu(run=subprocess.run) -> Gpu | None:
    """What nvidia-smi reports (name, VRAM, driver), whether or not CUDA 12 can use it. None: no NVIDIA GPU."""
    exe = _nvidia_smi()
    if not exe:
        return None
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = run([exe, *SMI_ARGS], capture_output=True, text=True, timeout=15, creationflags=flags).stdout
    except Exception:  # noqa: BLE001 - driver missing / broken
        return None
    return parse_smi(out)


def driver_too_old(gpu: Gpu | None, platform: str | None = None) -> bool:
    """The GPU is there but its driver predates CUDA 12 (#89/#95): it cannot run the models, use the CPU tier.
    Threshold: tinglongwu.device.MIN_DRIVER (Windows 528.33), the same check the runtime uses."""
    if gpu is None or not gpu.driver:
        return False
    from tinglongwu.device import driver_too_old as too_old

    return too_old(gpu.driver, platform or sys.platform)


def detect_gpu(run=subprocess.run, platform: str | None = None) -> Gpu | None:
    """A GPU the models can use, else None (CPU tier): no NVIDIA GPU, or a driver too old for CUDA 12."""
    gpu = probe_gpu(run)
    return None if driver_too_old(gpu, platform) else gpu


def driver_hint(gpu: Gpu, platform: str | None = None) -> str:
    from tinglongwu.device import DRIVER_URL, min_driver

    need = ".".join(map(str, min_driver(platform or sys.platform)))
    return (f"偵測到顯示卡 {gpu.name}，但 NVIDIA 驅動程式 {gpu.driver} 太舊（CUDA 12 需要 {need} 以上），"
            f"先使用 CPU 模式。更新驅動程式後重新設定即可使用 GPU：{DRIVER_URL}\n"
            f"NVIDIA driver {gpu.driver} is too old for CUDA 12 (needs {need}+): CPU mode for now. "
            f"Update the driver to use the GPU.")


def detection_text(raw: Gpu | None, force_cpu: bool = False, platform: str | None = None) -> str:
    """First-run setup window line about the GPU (`raw` = probe_gpu())."""
    if force_cpu:
        return "已指定 CPU 模式。"
    if raw is not None and driver_too_old(raw, platform):
        return driver_hint(raw, platform)
    if raw is not None:
        return f"偵測到顯示卡：{raw.name}（{raw.total_mb / 1024:.1f} GB 顯存）"
    return "沒有偵測到 NVIDIA 顯示卡，將使用 CPU 模式（速度較慢，但一樣能用）。"


def choose_tier(gpu: Gpu | None, force_cpu: bool = False) -> Tier:
    if force_cpu or gpu is None:
        return T_CPU
    return T_GPU if gpu.total_mb >= GPU_MIN_TOTAL_MB else T_MIN


# ---------------------------------------------------------------- what is installed
def installed_whisper(mdir: Path) -> list[str]:
    return [s for s in WHISPER_PREFERENCE if (mdir / f"whisper-{s}" / "model.bin").is_file()]


def models_ready(mdir: Path) -> bool:
    """At least one Whisper model, the en->zh OPUS pair and the Hy-MT2 GGUF (#301: tlwu stops without it, #276)."""
    mdir = Path(mdir)
    return (bool(installed_whisper(mdir)) and (mdir / "opus" / "en-zh" / "model" / "model.bin").is_file()
            and (mdir / HYMT_DIR / HYMT_FILE).is_file())


def best_installed_model(mdir: Path, wanted: str | None = None) -> str | None:
    have = installed_whisper(Path(mdir))
    if wanted in have:
        return wanted
    return have[0] if have else None


def dir_size(d: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(d):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def progress_fraction(done_bytes: int, tier: Tier) -> float:
    """Capped at 0.99: only the finished process makes it 1.0 (sizes are approximate)."""
    return max(0.0, min(0.99, done_bytes / max(1, tier.total_bytes)))


def default_models_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "tinglongwu" / "models"
