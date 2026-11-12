from __future__ import annotations

import sys

from .base import SAMPLE_RATE, AudioSource, to_mono_16k
from .file import FileSource
from .macos import MacLoopbackSource
from .mic import MicSource
from .pulse import PulseMonitorSource
from .wasapi import WasapiLoopbackSource

__all__ = ["AudioSource", "SAMPLE_RATE", "to_mono_16k", "select_backend"]


def select_backend(file: str | None = None, mic: bool = False,
                   device: str | None = None, platform: str | None = None) -> AudioSource:
    if file:
        return FileSource(file)
    if mic:
        return MicSource(device)
    platform = platform or sys.platform
    if platform.startswith("win"):
        return WasapiLoopbackSource(device)
    if platform.startswith("linux"):
        return PulseMonitorSource(device)
    if platform == "darwin":
        return MacLoopbackSource(device)
    raise RuntimeError(f"No loopback capture backend for platform {platform!r}; use --file")
