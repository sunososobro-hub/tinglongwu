"""Output sinks. Text goes to stdout only; nothing is logged or persisted."""
from __future__ import annotations

import sys
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Result:
    src: str
    original: str
    pivot: str | None
    target: str
    tgt: str = "zh"


class Sink(ABC):
    @abstractmethod
    def write(self, r: Result) -> None: ...

    def write_source(self, src: str, original: str) -> None:
        """#53: ASR finished, `original` is queued for MT; its write() follows later, in the same order.

        Called from the capture thread while write() runs on the MT thread. Default: ignore."""


class TextSink(Sink):
    """Append-only console blocks (no cursor moves, so redirected stdout stays readable).

    #53: write_source() prints the source line as soon as ASR is done when MT is idle; the [EN]/[ZH] lines follow
    under it when MT finishes. Lines heard while MT is busy wait their turn: each source line is printed right
    after the block before it, so every block still reads source-then-translations and nothing is repeated."""

    def __init__(self, stream=None, now=None, rec: str = ""):
        self.stream = stream
        self.now = now
        self.rec = rec  # #239: "● Recording" label shown at the start of every subtitle block while recording
        self._lock = threading.Lock()  # write_source (capture thread) vs write (MT thread)
        self._pending: list[list] = []  # [original, shown] heard, translation not written yet, oldest first
        self._open: str | None = None  # source line printed last, its translations not yet under it

    def _head(self, out, entry) -> None:
        from . import fmt

        out.write(self._mark(fmt.head("them", entry[0], now=self.now() if self.now else None,
                                      color=fmt.color_enabled(out))))
        entry[1] = True
        self._open = entry[0]

    def _mark(self, text: str) -> str:
        return f"{self.rec} {text}" if self.rec else text

    def write_source(self, src: str, original: str) -> None:
        with self._lock:
            out = self.stream or sys.stdout
            self._pending.append([original, False])
            if len(self._pending) == 1:  # MT idle: show it now
                self._head(out, self._pending[0])
                out.flush()

    def write(self, r: Result) -> None:
        from . import fmt

        tr = []
        if r.pivot and r.src != "en" and r.tgt != "en":
            tr.append(("EN", r.pivot))
        if r.src != r.tgt:
            tr.append((r.tgt.upper(), r.target))
        with self._lock:
            out = self.stream or sys.stdout
            if self._pending and self._pending[0][0] == r.original:
                self._pending.pop(0)
            if self._open is not None and self._open == r.original:
                out.write(fmt.tail(tr))  # right under its source line
            else:
                if self._open is not None:
                    out.write("\n")  # another line came first: end the dangling source line
                out.write(self._mark(fmt.block("them", r.original, tr, now=self.now() if self.now else None,
                                               color=fmt.color_enabled(out))))
            self._open = None
            if self._pending and not self._pending[0][1]:  # next heard line: its source now, translation later
                self._head(out, self._pending[0])
            out.flush()


class JsonlSaveSink(Sink):
    """Wraps a sink and appends every FINAL result to a UTF-8 JSONL file (flushed per line). Opt-in (--save)."""

    def __init__(self, inner: Sink, path, clock=None, now=None):
        import datetime
        import time
        from pathlib import Path

        self.inner, self.path = inner, Path(path)
        self.clock = clock or time.monotonic
        self.now = now or (lambda: datetime.datetime.now().astimezone())
        self.t0 = self.clock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "a", encoding="utf-8")

    def write_source(self, src: str, original: str) -> None:
        self.inner.write_source(src, original)

    def write(self, r: Result) -> None:
        import json

        self.inner.write(r)
        rec = {"t": round(self.clock() - self.t0, 2), "wall": self.now().isoformat(timespec="seconds"),
               "lang": r.src, "src": r.original, "dst": r.target if r.src != r.tgt else ""}
        self.f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.f.flush()


def default_save_path(now=None):
    import datetime

    return f"transcripts/{(now or datetime.datetime.now()).strftime('%Y%m%d-%H%M%S')}.jsonl"


class TranscriptSink(Sink):
    """Source-language text only, one segment per line (feed to scripts/wer.py)."""

    def __init__(self, stream=None, rec: str = ""):
        self.stream = stream
        self.rec = rec

    def write(self, r: Result) -> None:
        out = self.stream or sys.stdout
        out.write((self.rec + " " if self.rec else "") + " ".join(r.original.split()) + "\n")
        out.flush()


class TTSSink(Sink):
    """Planned: offline TTS via Piper (MIT) -> virtual mic (VB-Cable / BlackHole / PipeWire null-sink).

    Caveat: if the synthesized audio plays on the same device that tlwu captures, it is
    re-captured and re-translated (feedback loop). Needs echo exclusion: capture a
    different device, or mute/skip capture while TTS plays. Not implemented.
    """

    def write(self, r: Result) -> None:
        raise NotImplementedError("TTSSink is a roadmap stub (see README).")
