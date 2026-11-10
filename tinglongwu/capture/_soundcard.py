"""Shared implementation for live capture through the `soundcard` library (BSD-3).

soundcard talks to WASAPI (Windows), PulseAudio/PipeWire-pulse (Linux) and
CoreAudio (macOS). It is imported lazily so the package imports everywhere.
"""
from __future__ import annotations

import threading
import warnings
from typing import Iterator

import numpy as np

from .base import AudioSource, to_mono_16k

NATIVE_RATE = 48000
BLOCK = NATIVE_RATE // 10  # 100 ms


def suppress_capture_warnings() -> None:
    """Hide soundcard's "data discontinuity in recording" noise. soundcard's mediafoundation module runs
    warnings.simplefilter('always', SoundcardRuntimeWarning) at import, which lands in FRONT of any filter
    registered earlier, so this must be called again after `import soundcard` (filters are process-global,
    so it also covers the capture thread)."""
    warnings.filterwarnings("ignore", message=".*data discontinuity.*")
    warnings.filterwarnings("ignore", category=RuntimeWarning, module=r"soundcard(\..*)?$")


class SoundcardSource(AudioSource):
    def __init__(self, device: str | None = None):
        self.device = device
        self._stop = threading.Event()

    def _pick_microphone(self, sc):  # overridden per platform
        raise NotImplementedError

    def frames(self) -> Iterator[np.ndarray]:
        import soundcard as sc

        suppress_capture_warnings()
        mic = self._pick_microphone(sc)
        with mic.recorder(samplerate=NATIVE_RATE, channels=2) as rec:
            # one 100 ms block per iteration: close() is noticed within a block even in total silence,
            # and leaving the `with` releases the device (OS microphone indicator goes off).
            while not self._stop.is_set():
                data = rec.record(numframes=BLOCK)
                if self._stop.is_set():
                    break
                yield to_mono_16k(data, NATIVE_RATE)

    def close(self) -> None:
        self._stop.set()


def find_by_name(sc, name: str, include_loopback: bool = True):
    name = name.lower()
    for m in sc.all_microphones(include_loopback=include_loopback):
        if name in m.name.lower():
            return m
    raise RuntimeError(f"No capture device matching {name!r}. Available: "
                       f"{[m.name for m in sc.all_microphones(include_loopback=include_loopback)]}")
