from __future__ import annotations

from abc import ABC, abstractmethod
from math import gcd
from collections.abc import Iterator

import numpy as np

SAMPLE_RATE = 16000


class AudioSource(ABC):
    """Yields float32 mono 16 kHz numpy frames. Audio stays in memory only."""

    @abstractmethod
    def frames(self) -> Iterator[np.ndarray]: ...

    def close(self) -> None:
        pass


class AnyOf:
    """Event-like (is_set) that is true while the Event or any of the callables is true."""

    def __init__(self, *parts):
        self.parts = parts

    def is_set(self) -> bool:
        return any(p.is_set() if hasattr(p, "is_set") else p() for p in self.parts)


class MuteSource(AudioSource):
    """#1028 mic toggle: while `muted` (a threading.Event) is set, every frame of `inner` is replaced by silence of
    the same length. The device keeps running (no re-open, the stream clock stays right), but nothing said reaches
    ASR, so the speak leg sends nothing; the listen leg has its own source and is not touched."""

    def __init__(self, inner: AudioSource, muted):
        self.inner, self.muted = inner, muted

    def frames(self) -> Iterator[np.ndarray]:
        gen = self.inner.frames()
        try:
            for f in gen:
                yield np.zeros_like(f) if self.muted.is_set() else f
        finally:
            close = getattr(gen, "close", None)  # #10: end the capture generator on this thread
            if close is not None:
                close()

    def close(self) -> None:
        self.inner.close()


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
