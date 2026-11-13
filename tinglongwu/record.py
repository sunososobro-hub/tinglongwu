"""Opt-in local recording (#239 --record): one 16 kHz mono WAV per final line plus one segments.jsonl row per line
({file, start, end, wall, lang, source, translation}), so clips line up with subtitle lines (training data). Off by
default: nothing here runs and no audio file is written. Local files only; no network code."""
from __future__ import annotations

import datetime
import json
import threading
import wave
from pathlib import Path

import numpy as np

from .capture.base import SAMPLE_RATE


def default_record_dir(now=None) -> str:
    return f"transcripts/rec-{(now or datetime.datetime.now()).strftime('%Y%m%d-%H%M%S')}"


class Recorder:
    """add() / take() on the ASR thread, in segment order; line() on the MT thread. A segment whose text is split
    over two lines (a sentence ends inside it) goes into both clips, so a clip holds all of its line's speech."""

    def __init__(self, folder):
        self.dir, self.n = Path(folder), 0
        self.dir.mkdir(parents=True, exist_ok=True)
        self._f = open(self.dir / "segments.jsonl", "a", encoding="utf-8")
        self._lock = threading.Lock()  # Ctrl+C / Stop: close() can run while the MT thread is in line()
        self._segs: list[tuple] = []  # (number, audio, start s, end s) a later line may still need, oldest first

    def add(self, no: int, audio, start: float, end: float) -> None:
        self._segs.append((no, np.asarray(audio, dtype=np.float32), max(0.0, start), end))

    def take(self, first: int, last: int):
        """(audio, start, end) of a line holding text of segments first..last; keeps `last` for the next line."""
        took = [s for s in self._segs if first <= s[0] <= last]
        self._segs = [s for s in self._segs if s[0] >= last]
        return (np.concatenate([s[1] for s in took]), took[0][2], took[-1][3]) if took else None

    def line(self, clip, r) -> None:
        """Write `clip` as NNNN.wav and its jsonl row (r: the line's sink.Result)."""
        with self._lock:
            if clip is not None and not self._f.closed:
                self._write(clip, r)

    def _write(self, clip, r) -> None:
        self.n += 1
        name = f"{self.n:04d}.wav"
        with wave.open(str(self.dir / name), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes((np.clip(clip[0], -1.0, 1.0) * 32767.0).astype("<i2").tobytes())
        row = {"file": name, "start": round(clip[1], 2), "end": round(clip[2], 2),
               "wall": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "lang": r.src,
               "source": r.original, "translation": r.target if r.src != r.tgt else ""}
        self._f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._f.flush()

    def close(self) -> None:
        with self._lock:
            self._f.close()
