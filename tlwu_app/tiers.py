"""GPU detection, model tiers and download planning for the first-run setup. Pure logic, no tkinter, no network.

The tiers mirror install.ps1 (chosen by TOTAL VRAM): the runtime `--engine auto` re-checks free VRAM at start-up.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

MB = 1024 * 1024
WHISPER_PREFERENCE = ("large-v3-turbo", "large-v3", "medium", "small", "base", "tiny")

# approximate download sizes in bytes (from the pinned repos), used for the progress bar only
SIZES = {"whisper-small": 464 * MB, "whisper-large-v3-turbo": 1640 * MB, "opus": 250 * MB,
         "qwen3-4b": 2460 * MB, "qwen3-8b": 4800 * MB}


@dataclass(frozen=True)
class Tier:
    name: str            # gpu-high | gpu-mid | gpu-low | gpu-min | cpu
    whisper: str
    qwen: tuple[str, ...]  # downloader flags: ("--qwen",) | ("--qwen4b",) | ()
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
        return ["--whisper", self.whisper, "--langs", "ru,ja,en", "--to", "zh", *self.qwen]


def _tier(name, whisper, qwen, label, qparts):
    return Tier(name, whisper, qwen, label, (f"whisper-{whisper}", "opus", *qparts))


T_HIGH = _tier("gpu-high", "large-v3-turbo", ("--qwen",), "高階：最準確（Whisper large-v3-turbo + Qwen3-8B/4B）", ("qwen3-8b", "qwen3-4b"))
T_MID = _tier("gpu-mid", "large-v3-turbo", ("--qwen4b",), "中階（Whisper large-v3-turbo + Qwen3-4B）", ("qwen3-4b",))
T_LOW = _tier("gpu-low", "small", ("--qwen4b",), "入門（Whisper small + Qwen3-4B）", ("qwen3-4b",))
T_MIN = _tier("gpu-min", "small", (), "精簡（Whisper small + OPUS 翻譯）", ())
T_CPU = _tier("cpu", "small", (), "CPU 模式（Whisper small + OPUS 翻譯，較慢但不需顯示卡）", ())


@dataclass(frozen=True)
class Gpu:
    name: str
    total_mb: int
    free_mb: int


def parse_smi(text: str) -> Gpu | None:
    """First line of `nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader,nounits`."""
    for line in (text or "").splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) >= 3:
            try:
                return Gpu(f[0], int(f[1]), int(f[2]))
            except ValueError:
                return None
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


def detect_gpu(run=subprocess.run) -> Gpu | None:
    exe = _nvidia_smi()
    if not exe:
        return None
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = run([exe, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                  capture_output=True, text=True, timeout=15, creationflags=flags).stdout
    except Exception:  # noqa: BLE001 - driver missing / broken
        return None
    return parse_smi(out)


def choose_tier(gpu: Gpu | None, force_cpu: bool = False) -> Tier:
    if force_cpu or gpu is None:
        return T_CPU
    t = gpu.total_mb
    return T_HIGH if t >= 7500 else T_MID if t >= 5500 else T_LOW if t >= 3500 else T_MIN


# ---------------------------------------------------------------- what is installed
def installed_whisper(mdir: Path) -> list[str]:
    return [s for s in WHISPER_PREFERENCE if (mdir / f"whisper-{s}" / "model.bin").is_file()]


def models_ready(mdir: Path) -> bool:
    """At least one Whisper model and the en->zh OPUS pair (the minimum for a working translation)."""
    mdir = Path(mdir)
    return bool(installed_whisper(mdir)) and (mdir / "opus" / "en-zh" / "model" / "model.bin").is_file()


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
