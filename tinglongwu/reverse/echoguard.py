"""Echo guard for the listen leg (#1070): our translated TTS plays in the remote party's room, their mic picks it up
and it returns through the system-audio loopback 0.5-3 s later. The speak leg records what it sent (spoken target
text + its source); a listen segment that arrives inside the window and matches one of them is dropped.

Matching is on normalized text (NFKC, casefold, no punctuation): words for spaced scripts, characters for CJK.
A segment is an echo when the whole-text ratio >= RATIO, or (partial / truncated echo, >= MIN_TOKENS tokens) when
>= COVER of the segment is found, in order, inside one spoken line. A real reply that merely shares a few words
("Yes, I can hear you now" after "Can you hear me?") stays well below both."""
from __future__ import annotations

import re
import sys
import threading
import time
import unicodedata
from difflib import SequenceMatcher

WINDOW_S = 8.0  # after the line finished playing (a line still playing never expires)
RATIO = 0.6
COVER = 0.8
MIN_TOKENS = 3  # coverage rule needs this many tokens; shorter segments need the full ratio
_CJK = "぀-ヿ㐀-鿿가-힯"
_TOK = re.compile(rf"[{_CJK}]|[^\W_{_CJK}]+")


def tokens(text: str) -> list[str]:
    return _TOK.findall(unicodedata.normalize("NFKC", text or "").casefold())


def similarity(heard: str, spoken: str) -> float:
    a, b = tokens(heard), tokens(spoken)
    if not a or not b:
        return 0.0
    sm = SequenceMatcher(None, a, b, autojunk=False)
    score = sm.ratio()
    if len(a) >= MIN_TOKENS:
        matched = sum(m.size for m in sm.get_matching_blocks())
        score = max(score, RATIO if matched / len(a) >= COVER else 0.0)
    return score


class EchoGuard:
    def __init__(self, window: float = WINDOW_S, ratio: float = RATIO, clock=time.monotonic, log=None):
        self.window, self.ratio, self.clock = window, ratio, clock
        self.log = log or (lambda m: print(m, file=sys.stderr, flush=True))
        self._lock = threading.Lock()
        self._items: list[list] = []  # [text, end_time or None]

    def _prune(self, now: float) -> None:
        self._items = [i for i in self._items if i[1] is None or now - i[1] <= self.window]

    def started(self, text: str):
        """A line (target or source text) is queued / being spoken; returns a handle for finished()."""
        if not tokens(text):
            return None
        item = [text, None]
        with self._lock:
            self._prune(self.clock())
            self._items.append(item)
        return item

    def finished(self, item) -> None:
        if item is not None:
            with self._lock:
                item[1] = self.clock()

    def note(self, text: str) -> None:
        """A line known only by its text (the source side): the window starts now."""
        self.finished(self.started(text))

    def is_echo(self, *texts: str) -> bool:
        with self._lock:
            self._prune(self.clock())
            spoken = [i[0] for i in self._items]
        return any(similarity(t, s) >= self.ratio for t in texts if t for s in spoken)

    def check(self, *texts: str) -> bool:
        if self.is_echo(*texts):
            self.log("tlwu: listen gate: dropped echo")
            return True
        return False


_shared = EchoGuard()


def shared() -> EchoGuard:
    return _shared
