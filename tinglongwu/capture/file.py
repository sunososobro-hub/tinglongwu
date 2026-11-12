from __future__ import annotations

from typing import Iterator

import numpy as np

from .base import SAMPLE_RATE, AudioSource, to_mono_16k


class FileSource(AudioSource):
    """Audio file -> 0.5 s frames at 16 kHz mono. Decoded in memory, no temp files.

    wav/flac/ogg via soundfile; anything else (mp3, m4a, aac, opus, mp4...) via PyAV.
    """

    def __init__(self, path: str, block_s: float = 0.5):
        self.path = path
        self.block = int(block_s * SAMPLE_RATE)

    def _load(self) -> np.ndarray:
        import soundfile as sf

        try:
            data, rate = sf.read(self.path, dtype="float32", always_2d=True)
            return to_mono_16k(data, rate)
        except (sf.LibsndfileError, RuntimeError):
            return self._load_av()

    def _load_av(self) -> np.ndarray:
        import av

        parts = []
        with av.open(self.path) as c:
            rs = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
            for frame in c.decode(audio=0):
                for f in rs.resample(frame):
                    parts.append(f.to_ndarray().reshape(-1))
            for f in rs.resample(None):
                parts.append(f.to_ndarray().reshape(-1))
        return np.concatenate(parts).astype(np.float32) if parts else np.zeros(0, np.float32)

    def frames(self) -> Iterator[np.ndarray]:
        audio = self._load()
        for i in range(0, len(audio), self.block):
            yield audio[i : i + self.block]
