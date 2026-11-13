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
    side: str = "them"  # #373: "them" = heard (listen side), "me" = what I said (speak side, meeting mode)
    speaker: str | None = None  # #384 --speakers: auto label ("說話者2") of the line, None if off or unknown


def pass_source(sink, src: str, original: str, speaker: str | None = None) -> None:
    """#384: sink.write_source(); the speaker keyword only when set (sinks without --speakers support stay valid)."""
    sink.write_source(src, original, **({"speaker": speaker} if speaker else {}))


class Sink(ABC):
    @abstractmethod
    def write(self, r: Result) -> None: ...

    def write_source(self, src: str, original: str, speaker: str | None = None) -> None:
        """#53: ASR finished, `original` is queued for MT; its write() follows later, in the same order.

        Called from the capture thread while write() runs on the MT thread. Default: ignore."""


class TextSink(Sink):
    """Append-only console blocks (no cursor moves, so redirected stdout stays readable).

    #53: write_source() prints the source line as soon as ASR is done when MT is idle; the [EN]/[ZH] lines follow
    under it when MT finishes. Lines heard while MT is busy wait their turn: each source line is printed right
    after the block before it, so every block still reads source-then-translations and nothing is repeated."""

    def __init__(self, stream=None, now=None, names=None, rec: str = ""):
        self.stream = stream
        self.now = now
        self.rec = rec  # #239 --record: "● Recording" mark at the start of every subtitle block
        self.names = names  # #384: a speakers.SpeakerBook; its display() maps a label to the user-given name
        self._lock = threading.Lock()  # write_source (capture thread) vs write (MT thread)
        self._pending: list[list] = []  # [original, shown, speaker] heard, translation not written yet, oldest first
        self._open: str | None = None  # source line printed last, its translations not yet under it

    def _head(self, out, entry) -> None:
        from . import fmt

        out.write(self._mark(fmt.head("them", entry[0], now=self.now() if self.now else None,
                                      color=fmt.color_enabled(out), who=self._who(entry[2]))))
        entry[1] = True
        self._open = entry[0]

    def _mark(self, text: str) -> str:
        return f"{self.rec} {text}" if self.rec else text

    def _who(self, speaker):
        return self.names.display(speaker) if self.names is not None and speaker else speaker

    def write_source(self, src: str, original: str, speaker: str | None = None) -> None:
        with self._lock:
            out = self.stream or sys.stdout
            self._pending.append([original, False, speaker])
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
                                               color=fmt.color_enabled(out), who=self._who(r.speaker))))
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
        self._lock = threading.Lock()  # #373: meeting mode records both sides (listen thread + speak thread)
        self.on_line = None  # #609 --topics: called with every saved record (topics.TopicNotes.on_line)

    def write_source(self, src: str, original: str, speaker: str | None = None) -> None:
        pass_source(self.inner, src, original, speaker)

    def write(self, r: Result) -> None:
        self.inner.write(r)
        self.record(r)

    def record(self, r: Result) -> None:
        """Append `r` to the file only (no console). #373: the speak side's lines go here with side="me"."""
        import json

        rec = {"t": round(self.clock() - self.t0, 2), "wall": self.now().isoformat(timespec="seconds"),
               "side": r.side, "lang": r.src, "src": r.original, "dst": r.target if r.src != r.tgt else ""}
        if r.speaker:
            rec["speaker"] = r.speaker  # #384: the auto label; corrections follow as "fix" lines (write_fix)
        with self._lock:
            self.f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.f.flush()
        if self.on_line is not None:
            try:
                self.on_line(rec)
            except Exception as e:  # noqa: BLE001 - topic notes must never break the transcript
                print(f"tlwu: topic notes: {e}", file=sys.stderr)

    def write_fix(self, event: dict) -> None:
        """#384: append a speaker correction (speakers.SpeakerBook.apply format); a "relabel" index counts the
        result lines of this file from 0. Replayed by scripts/eval_diarization.py --transcript."""
        import json

        with self._lock:
            self.f.write(json.dumps({"t": round(self.clock() - self.t0, 2), "fix": event}, ensure_ascii=False) + "\n")
            self.f.flush()


def default_save_path(now=None):
    import datetime

    return f"transcripts/{(now or datetime.datetime.now()).strftime('%Y%m%d-%H%M%S')}.jsonl"


class TranscriptSink(Sink):
    """Source-language text only, one segment per line (feed to scripts/wer.py)."""

    def __init__(self, stream=None, rec: str = ""):
        self.stream = stream
        self.rec = rec  # #239 --record mark

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
