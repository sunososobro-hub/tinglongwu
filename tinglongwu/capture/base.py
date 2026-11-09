from __future__ import annotations

from abc import ABC, abstractmethod
from math import gcd
from typing import Iterator

import numpy as np

SAMPLE_RATE = 16000


class AudioSource(ABC):
    """Yields float32 mono 16 kHz numpy frames. Audio stays in memory only."""

    @abstractmethod
    def frames(self) -> Iterator[np.ndarray]: ...

    def close(self) -> None:
        pass


def to_mono_16k(data: np.ndarray, rate: int) -> np.ndarray:
    """(n,) or (n, ch) array at `rate` -> float32 mono at 16 kHz."""
    data = np.asarray(data, dtype=np.float32)
    if data.ndim == 2:
        data = data.mean(axis=1)
    if rate != SAMPLE_RATE:
        from scipy.signal import resample_poly

        g = gcd(rate, SAMPLE_RATE)
        data = resample_poly(data, SAMPLE_RATE // g, rate // g).astype(np.float32)
    return np.ascontiguousarray(data, dtype=np.float32)
