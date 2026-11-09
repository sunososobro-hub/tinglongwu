"""Shared implementation for live capture through the `soundcard` library (BSD-3).

soundcard talks to WASAPI (Windows), PulseAudio/PipeWire-pulse (Linux) and
CoreAudio (macOS). It is imported lazily so the package imports everywhere.
"""
from __future__ import annotations

from typing import Iterator

import numpy as np

from .base import AudioSource, to_mono_16k

NATIVE_RATE = 48000
BLOCK = NATIVE_RATE // 10  # 100 ms


class SoundcardSource(AudioSource):
    def __init__(self, device: str | None = None):
        self.device = device

    def _pick_microphone(self, sc):  # overridden per platform
        raise NotImplementedError

    def frames(self) -> Iterator[np.ndarray]:
        import soundcard as sc

        mic = self._pick_microphone(sc)
        with mic.recorder(samplerate=NATIVE_RATE, channels=2) as rec:
            while True:
                yield to_mono_16k(rec.record(numframes=BLOCK), NATIVE_RATE)


def find_by_name(sc, name: str, include_loopback: bool = True):
    name = name.lower()
    for m in sc.all_microphones(include_loopback=include_loopback):
        if name in m.name.lower():
            return m
    raise RuntimeError(f"No capture device matching {name!r}. Available: "
                       f"{[m.name for m in sc.all_microphones(include_loopback=include_loopback)]}")
