"""#280: minimum hardware spec. Machines below it fail clearly instead of running slowly in silence.

- `check_avx2()`: the CPU must have AVX2 (the CTranslate2 / llama.cpp CPU builds assume it); `--force` skips the check.
- `SlowWatch`: on CPU only, after 3 segments, one warning when (ASR + MT) p50 per segment is more than half
  the segment length (live subtitles will lag).
Local only, no network. An unknown answer (non-x86 CPU, unreadable /proc/cpuinfo) never blocks.
"""
from __future__ import annotations

import statistics
import sys

from .report import PF_AVX2, _win_cpu

SLOW_SEGMENTS = 3  # segments to see before judging
SLOW_RATIO = 0.5  # (ASR + MT) p50 above segment length x this = below spec


def cpu_has_avx2(system: str | None = None, cpuinfo="/proc/cpuinfo", win=_win_cpu) -> bool | None:
    """True / False when the OS answered, None when unknown (Apple Silicon / ARM, unreadable cpuinfo)."""
    if (system or sys.platform) == "win32":
        try:
            _, feats = win()
        except Exception:  # noqa: BLE001
            return None
        return None if feats is None else "AVX2" in feats
    try:
        with open(cpuinfo, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("flags") and ":" in line:
                    return "avx2" in line.split(":", 1)[1].split()
    except OSError:
        pass
    return None


def check_avx2(force: bool = False, has_avx2=cpu_has_avx2) -> None:
    """Raise RuntimeError('not supported: CPU lacks AVX2 ...') on a CPU that answered 'no AVX2' unless forced."""
    if has_avx2() is False and not force:
        raise RuntimeError("not supported: CPU lacks AVX2 (--force to continue anyway)")


class SlowWatch:
    """observe(asr_s, mt_s, seg_s) once per output line; warns once through say() when this CPU is too slow."""

    def __init__(self, say=None, n: int = SLOW_SEGMENTS, ratio: float = SLOW_RATIO):
        self.say = say or (lambda m: print(m, file=sys.stderr, flush=True))
        self.n, self.ratio = n, ratio
        self.work: list[float] = []
        self.audio: list[float] = []
        self.warned = False

    def observe(self, asr_s: float, mt_s: float, seg_s: float) -> bool:
        if self.warned or seg_s <= 0:
            return False
        self.work.append(asr_s + mt_s)
        self.audio.append(seg_s)
        if len(self.work) < self.n:
            return False
        if statistics.median(self.work) > statistics.median(self.audio) * self.ratio:
            self.warned = True
            self.say("tlwu: this machine is below the recommended spec (see README, System requirements): "
                     "live subtitles will lag")
            return True
        return False
