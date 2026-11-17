"""Opt-in local recording (#239 --record): one 16 kHz mono WAV per final line plus one segments.jsonl row per line
({file, start, end, wall, lang, source, translation}), so clips line up with subtitle lines (training data). Off by
default: nothing here runs and no audio file is written. Local files only; no network code."""
from __future__ import annotations

import datetime
import json
import os
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
        self._done = 0  # newest segment number an earlier line already took
        self._last = None  # (wav name, clip, jsonl size before its row) of the newest line written: #1395 may amend it

    def add(self, no: int, audio, start: float, end: float) -> None:
        self._segs.append((no, np.asarray(audio, dtype=np.float32), max(0.0, start), end))

    def take(self, first: int, last: int, amend: bool = False):
        """(audio, start, end) of a line holding text of segments first..last; keeps `last` for the next line.
        #1252: a segment older than `first` that no earlier line took (its whole text was the VAD overlap the merger
        de-duplicated) goes into this line too, so its audio still maps to a line instead of being thrown away.
        #1395 amend=True: the clip is joined to the previous line's clip, so it keeps only segments newer than the
        newest one that line took (a segment shared by both lines would be heard twice)."""
        if amend:
            first = max(first, self._done + 1)
        took = [s for s in self._segs if (first <= s[0] or s[0] > self._done) and s[0] <= last]
        self._segs = [s for s in self._segs if s[0] >= last]
        self._done = max(self._done, last)
        return (np.concatenate([s[1] for s in took]), took[0][2], took[-1][3]) if took else None

    def line(self, clip, r) -> None:
        """Write `clip` as NNNN.wav and its jsonl row (r: the line's sink.Result).
        #1395: a line that replaces the previous one (previous text + a late fragment) rewrites that line's row and
        clip with both clips joined, so a row's `source` always matches the audio in its WAV."""
        with self._lock:
            if self._f.closed:
                return
            replacing = getattr(r, "replaces", None) is not None
            amend = replacing and self._last is not None
            if replacing and not amend:
                self._last = None  # no previous clip to join: never pair part of the speech with the whole text
                return
            if clip is None and not amend:
                self._last = None
                return
            self._f.flush()
            if amend:
                name, prev, offset = self._last
                # clip is None: take() found every segment already in the previous clip, so that audio is the fragment's too
                clip = prev if clip is None else (np.concatenate([prev[0], clip[0]]), prev[1], clip[2])
            else:
                self.n += 1
                name = f"{self.n:04d}.wav"
                offset = (self.dir / "segments.jsonl").stat().st_size
            row = {"file": name, "start": round(clip[1], 2), "end": round(clip[2], 2),
                   "wall": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "lang": r.src,
                   "source": r.original, "translation": r.target if r.src != r.tgt else ""}
            # stage the WAV first: if it fails (disk full) the previous row and clip stay as they were
            tmp = self.dir / (name + ".tmp")
            try:
                with wave.open(str(tmp), "wb") as w:
                    w.setnchannels(1), w.setsampwidth(2), w.setframerate(SAMPLE_RATE)
                    w.writeframes((np.clip(clip[0], -1.0, 1.0) * 32767.0).astype("<i2").tobytes())
                os.replace(tmp, self.dir / name)  # the last step that can fail on a full disk or a locked file
                if amend:
                    self._f.truncate(offset)  # drop the previous line's row; the new row below replaces it
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            self._f.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._f.flush()
            self._last = (name, clip, offset)

    def close(self) -> None:
        with self._lock:
            self._f.close()
