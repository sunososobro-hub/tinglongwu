"""#833: holds each translated speak line in the GUI until the shared SendDecider (#832) says send.

I/O-free: lines go in through offer(), the result goes out through `release(text)` (the voice queue) and
`on_change(view)` (the GUI redraw); time comes from the injected clock. The GUI calls tick() about every 100 ms and
confirm() / stop() from its Send / Stop buttons and the Space / Esc keys. One line is held at a time; newer lines
queue behind it (at most MAX_QUEUED, the oldest waiting one is dropped: a stale line is worse than a missing one).
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

from . import sendmode as _sm
from .confirm import Utterance

MAX_QUEUED = 3  # lines waiting behind the held one


@dataclass
class GateView:
    """What the GUI shows: the held line, whether it needs a confirm, seconds left in countdown mode."""
    text: str = ""
    state: str = "idle"  # idle | counting | asking
    reason: str = ""
    remaining: float = 0.0
    queued: int = 0


class SendGate:
    def __init__(self, release, clock, mode: str = "confirm", countdown_s: float | None = None, on_change=None):
        self.release, self.clock, self.mode, self.countdown_s = release, clock, mode, countdown_s
        self.on_change = on_change or (lambda v: None)
        self.sent = self.discarded = self.dropped = 0
        self._lock = threading.Lock()
        self._waiting: deque = deque()
        self._dec: _sm.SendDecider | None = None
        self._text = ""

    # -- inputs -----------------------------------------------------------------------------------
    def offer(self, zh: str, text: str, extra: dict | None = None) -> None:
        with self._lock:
            self._waiting.append(Utterance(0, zh, text, extra=dict(extra or {})))
            while len(self._waiting) > MAX_QUEUED:
                self._waiting.popleft()
                self.dropped += 1
            self._advance()
        self._emit()

    def tick(self) -> None:
        with self._lock:
            if self._dec is not None and self._dec.state == "counting":
                if self._dec.tick() == _sm.SEND:
                    self._finish(True)
                    self._advance()
        self._emit()

    def confirm(self) -> None:
        with self._lock:
            if self._dec is not None:
                self._dec.confirm()
                self._finish(True)
                self._advance()
        self._emit()

    def stop(self) -> None:
        """Discard the held line (Stop button / Space / Esc)."""
        with self._lock:
            if self._dec is not None:
                self._dec.stop()
                self._finish(False)
                self._advance()
        self._emit()

    def clear(self) -> None:
        """Session end: nothing is sent any more."""
        with self._lock:
            self._waiting.clear()
            self._dec, self._text = None, ""
        self._emit()

    # -- internals (lock held) ----------------------------------------------------------------------
    def _finish(self, sent: bool) -> None:
        text, self._dec, self._text = self._text, None, ""
        if sent:
            self.sent += 1
            self.release(text)
        else:
            self.discarded += 1

    def _advance(self) -> None:
        while self._dec is None and self._waiting:
            u = self._waiting.popleft()
            dec = _sm.SendDecider(self.clock, self.mode, self.countdown_s, require_back=False)
            if dec.line_ready(u) == _sm.SEND:
                self.sent += 1
                self.release(u.ru)
                continue
            self._dec, self._text = dec, u.ru

    def view(self) -> GateView:
        with self._lock:
            d = self._dec
            if d is None:
                return GateView()
            left = max(0.0, d._end - self.clock()) if d.state == "counting" else 0.0
            return GateView(self._text, d.state, d.reason, left, len(self._waiting))

    def _emit(self) -> None:
        self.on_change(self.view())
