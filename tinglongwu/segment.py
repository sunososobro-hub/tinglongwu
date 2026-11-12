"""Chunking: Silero VAD pause splitting (default) or fixed blocks. In-memory only."""
from __future__ import annotations

from functools import partial
from typing import Callable, Iterable, Iterator

import numpy as np

from .capture.base import SAMPLE_RATE as SR

VadFn = Callable[[np.ndarray], list[tuple[int, int]]]


def silero_vad(audio: np.ndarray, pad_ms: int = 400) -> list[tuple[int, int]]:
    """Speech spans (sample offsets) using the Silero ONNX model bundled in faster-whisper. Each span end is padded
    by `pad_ms` (faster-whisper's default 400), so a pause is only seen `pad_ms` after the speech really ended."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    opts = VadOptions(min_silence_duration_ms=300, speech_pad_ms=pad_ms)
    return [(t["start"], t["end"]) for t in get_speech_timestamps(audio, opts)]


class Segmenter:
    def __init__(self, chunk: float = 10.0, use_vad: bool = True, overlap: float = 0.5,
                 min_silence: float = 0.6, step: float = 0.5, vad_fn: VadFn | None = None,
                 vad_pad: float | None = None):
        self.chunk, self.use_vad = int(chunk * SR), use_vad
        self.ov, self.min_sil, self.step = int(overlap * SR), int(min_silence * SR), int(step * SR)
        # vad_pad (#186, live speak): Silero span padding in seconds instead of the 0.4 s default. The pad delays
        # every cut by itself, so it is also added to last_pause (= real silence after the speech). None = unchanged.
        self.pad = 0.0 if vad_pad is None else float(vad_pad)
        self.vad = vad_fn or (silero_vad if vad_pad is None else partial(silero_vad, pad_ms=int(round(vad_pad * 1000))))
        # in-progress speech for --stream drafts: (epoch, audio) or None. epoch changes at every emitted segment.
        self.epoch = 0
        # how the segment just yielded was cut: forced (chunk limit, speech goes on) or at a pause; and the
        # silence (s) seen after its speech. Read by the consumer right after each yield.
        self.last_forced, self.last_pause = False, 0.0
        self.live: tuple[int, np.ndarray] | None = None

    def segments(self, frames: Iterable[np.ndarray]) -> Iterator[np.ndarray]:
        buf, since, fresh = np.zeros(0, np.float32), 0, 0
        for f in frames:
            buf = np.concatenate([buf, f])
            since += len(f)
            fresh += len(f)
            if since < self.step:
                continue
            since = 0
            out, buf = self._step(buf)
            if out is not None:
                self.epoch += 1
                self.live = None
                yield out
                fresh = 0
        self.live = None
        self.last_forced, self.last_pause = False, 0.0
        tail = self._flush(buf, fresh)
        if tail is not None:
            yield tail

    def _step(self, buf):
        if not self.use_vad:
            if len(buf) >= self.chunk:
                self.last_forced, self.last_pause = True, 0.0
                return buf, buf[-self.ov:]
            self.live = (self.epoch, buf[-self.chunk:])
            return None, buf
        spans = self.vad(buf)
        if not spans:
            self.live = None
            return None, buf[-self.ov:] if len(buf) > 2 * self.ov else buf
        s, e = spans[0][0], spans[-1][1]
        if len(buf) - e >= self.min_sil:  # pause after speech: cut here
            self.last_forced, self.last_pause = False, (len(buf) - e) / SR + self.pad
            return buf[max(0, s - self.ov): min(len(buf), e + self.ov)], buf[e:]
        if len(buf) - s >= self.chunk:  # too long without a pause: force cut, keep overlap
            self.last_forced, self.last_pause = True, 0.0
            return buf[s:], buf[-self.ov:]
        keep = buf[max(0, s - self.ov):]  # drop leading silence
        self.live = (self.epoch, keep[-self.chunk:])
        return None, keep

    def _flush(self, buf, fresh):
        if self.use_vad:
            spans = self.vad(buf) if len(buf) else []
            if spans:  # #323: the silence after the speech at end of input (shorter than min_silence)
                self.last_pause = (len(buf) - spans[-1][1]) / SR + self.pad
            return buf[max(0, spans[0][0] - self.ov):] if spans else None
        return buf if fresh > SR // 2 else None
