"""Runtime auto-downgrade (#80, part 3 of #63): when lines arrive too late, step down one model; step back up
when things have been fast for a while.

Latency of a line = ASR time of its last segment + wait in the MT queue + MT time (the per-stage numbers of
--debug-latency, #35), i.e. speech end -> translated line without the sentence-merge hold, which is a choice
and not slowness. Order (#80): translation engine first, then the speech model; back up in reverse order.
"""
from __future__ import annotations

import sys

# Budget: #80 / #63 part 3 ("start at 2 s").
BUDGET_S = 2.0
# Lines in a row over budget before a step down. My choice (#80 says "N"): 1-2 slow lines are normal (a long
# sentence, a GC pause); 3 in a row is a trend, and at ~1 line per 3-5 s it reacts within ~15 s.
N_OVER = 3
# Step back up after this many lines in a row under UP_FRACTION of the budget. My choice: ~2 minutes of
# speech; doubled after every step up that had to be undone (so a machine that cannot cope stops retrying),
# capped at STABLE_MAX.
STABLE_LINES = 30
STABLE_MAX = 480
UP_FRACTION = 0.5
# Whisper sizes from slowest to fastest; a step down goes to the next installed one to the right.
ASR_ORDER = ["large-v3", "medium", "large-v3-turbo", "small", "base", "tiny"]


class Ladder:
    """One switchable component: the loaded object plus lazily built lower (faster) alternatives."""

    def __init__(self, kind: str, label: str, obj, lower=()):
        self.kind = kind
        self.steps = [[label, obj, None]] + [[lab, None, make] for lab, make in lower]
        self.i = 0

    @property
    def current(self):
        return self.steps[self.i][1]

    @property
    def label(self) -> str:
        return self.steps[self.i][0]

    def down(self) -> bool:
        """Switch to the next lower step that can be built; False when there is none (a step whose model is
        missing or fails to load is dropped for good)."""
        while self.i + 1 < len(self.steps):
            step = self.steps[self.i + 1]
            if step[1] is None:
                try:
                    step[1] = step[2]()
                except Exception:  # noqa: BLE001 - model not installed / does not load: not a candidate
                    del self.steps[self.i + 1]
                    continue
            self.i += 1
            return True
        return False

    def up(self) -> bool:
        if self.i == 0:
            return False
        self.i -= 1
        return True

    def drop_current(self) -> bool:
        """The current lower step turned out unusable: go back up and forget it."""
        if self.i == 0:
            return False
        del self.steps[self.i]
        self.i -= 1
        return True


class Switchable:
    """Stands in for the ASR / MT object: every attribute comes from the ladder's current step, so the
    pipeline and the draft engine keep one reference while the model behind it changes."""

    def __init__(self, ladder: Ladder):
        self._ladder = ladder

    def __getattr__(self, name):
        return getattr(self._ladder.current, name)


class SwitchableTranslator(Switchable):
    def translate(self, text, src, tgt, *a, **k):
        from .models import ModelNotFound

        while True:
            try:
                return self._ladder.current.translate(text, src, tgt, *a, **k)
            except ModelNotFound:  # the lower engine lacks this language pair: never lose the line over it
                if not self._ladder.drop_current():
                    raise


class LatencyController:
    """observe(latency_s) once per output line; returns 'down', 'up' or None. `ladders` in step-down order."""

    def __init__(self, ladders, budget: float = BUDGET_S, n: int = N_OVER, stable: int = STABLE_LINES,
                 log=None):
        self.ladders, self.budget, self.n, self.stable = list(ladders), budget, n, stable
        self.log = log or (lambda m: print(m, file=sys.stderr, flush=True))
        self.over = self.fast = self.skip = 0
        self.downs: list[Ladder] = []  # ladders stepped down, newest last
        self.probing = False  # the last switch was a step up that has not proven itself yet

    def _switched(self, lad: Ladder, old: str, why: str) -> None:
        self.over = self.fast = 0
        self.skip = self.n  # lines already queued behind the old model say nothing about the new one
        self.log(f"tlwu: {why}: {lad.kind} {old} -> {lad.label}")

    def observe(self, latency: float) -> str | None:
        if self.skip:
            self.skip -= 1
            return None
        if latency > self.budget:
            self.over, self.fast = self.over + 1, 0
            if self.over < self.n:
                return None
            self.over = 0
            if self.probing:  # the step up did not hold: wait twice as long before the next try
                self.stable, self.probing = min(self.stable * 2, STABLE_MAX), False
            for lad in self.ladders:
                old = lad.label
                if lad.down():
                    self.downs.append(lad)
                    self._switched(lad, old, f"latency over {self.budget:g} s for {self.n} lines, stepping down")
                    return "down"
            return None
        self.over = 0
        if latency > self.budget * UP_FRACTION:
            self.fast = 0
            return None
        self.fast += 1
        if self.fast < self.stable:
            return None
        self.fast, self.probing = 0, False  # a full stable period: the current level is proven
        while self.downs:
            lad = self.downs.pop()
            old = lad.label
            if not lad.up():  # already back at the top (its lower step was dropped as unusable): nothing to undo
                continue
            self.probing = True
            self._switched(lad, old, f"stable for {self.stable} lines, stepping back up")
            return "up"
        return None


def smaller_whisper(current: str, installed) -> list[str]:
    """Installed Whisper sizes that are faster than `current`, nearest first."""
    if current not in ASR_ORDER:
        return []
    return [s for s in ASR_ORDER[ASR_ORDER.index(current) + 1:] if s in installed]
