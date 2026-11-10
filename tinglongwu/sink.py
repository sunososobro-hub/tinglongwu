"""Output sinks. Text goes to stdout only; nothing is logged or persisted."""
from __future__ import annotations

import sys
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


class TextSink(Sink):
    def __init__(self, stream=None, now=None):
        self.stream = stream
        self.now = now

    def write(self, r: Result) -> None:
        from . import fmt

        tr = []
        if r.pivot and r.src != "en" and r.tgt != "en":
            tr.append(("EN", r.pivot))
        if r.src != r.tgt:
            tr.append((r.tgt.upper(), r.target))
        out = self.stream or sys.stdout
        out.write(fmt.block("them", r.original, tr, now=self.now() if self.now else None,
                            color=fmt.color_enabled(out)))
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

    def __init__(self, stream=None):
        self.stream = stream

    def write(self, r: Result) -> None:
        out = self.stream or sys.stdout
        out.write(" ".join(r.original.split()) + "\n")
        out.flush()


class TTSSink(Sink):
    """Planned: offline TTS via Piper (MIT) -> virtual mic (VB-Cable / BlackHole / PipeWire null-sink).

    Caveat: if the synthesized audio plays on the same device that tlwu captures, it is
    re-captured and re-translated (feedback loop). Needs echo exclusion: capture a
    different device, or mute/skip capture while TTS plays. Not implemented.
    """

    def write(self, r: Result) -> None:
        raise NotImplementedError("TTSSink is a roadmap stub (see README).")
