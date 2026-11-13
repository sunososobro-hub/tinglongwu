"""#608 (part 1 of #595): split a live meeting transcript into closed topic segments.

Pure and single-threaded (the caller serializes add()/flush(), as topics.TopicNotes does with its lock).
A topic closes when, with the open topic already >= min_s long,
  - the next line comes after a pause >= pause_s, or
  - the next line's embedding is less similar than sim_threshold (cosine) to the mean of the open topic's
    last `window` line embeddings (only when an `embed(text) -> vector` function is injected);
and a topic is always cut before it would span more than max_s.

Topic length is first-line t to last-line t. Every closed topic is >= min_s long except (a) the flush() at meeting
end and (b) a short topic followed by a silence so long that adding the next line would pass max_s: max_s wins
there, since one topic must never swallow a 5-minute silence.
"""
from __future__ import annotations

import math
from typing import Callable, Sequence

PAUSE_S = 8.0          # #595: a pause this long closes a topic (once it is at least MIN_S long)
MIN_S = 60.0           # #595: min ~60 s per topic (the meeting-end flush may be shorter)
MAX_S = 300.0          # #595: max ~5 min per topic
SIM_THRESHOLD = 0.35   # cosine; below this the new line starts a new topic. Tune with the #610 bake-off model.
WINDOW = 4             # compare against the mean of the last N lines of the open topic


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class TopicSegmenter:
    """add(line) -> list of closed topics (each a list of lines); flush() -> the open topic (meeting end).
    Lines need `.t` (seconds) and `.text`. All thresholds are constructor parameters."""

    def __init__(self, pause_s: float = PAUSE_S, min_s: float = MIN_S, max_s: float = MAX_S,
                 embed: Callable[[str], Sequence[float]] | None = None, sim_threshold: float = SIM_THRESHOLD,
                 window: int = WINDOW):
        if not 0 <= min_s <= max_s:
            raise ValueError(f"need 0 <= min_s <= max_s, got {min_s}, {max_s}")
        self.pause_s, self.min_s, self.max_s = pause_s, min_s, max_s
        self.embed, self.sim_threshold, self.window = embed, sim_threshold, max(1, int(window))
        self.cur: list = []
        self._vecs: list = []  # embeddings of self.cur's lines (None when embed failed / is off)

    def _vec(self, text: str):
        if self.embed is None:
            return None
        try:
            return list(self.embed(text))
        except Exception:  # an embedding error must not break the transcript; pause/max rules still apply
            return None

    def _topic_shift(self, vec) -> bool:
        recent = [v for v in self._vecs[-self.window:] if v is not None]
        if vec is None or not recent:
            return False
        centroid = [sum(col) / len(recent) for col in zip(*recent)]
        return cosine(vec, centroid) < self.sim_threshold

    def add(self, line) -> list[list]:
        out = []
        vec = self._vec(line.text)
        if self.cur:
            dur = self.cur[-1].t - self.cur[0].t
            too_long = line.t - self.cur[0].t > self.max_s
            pause = line.t - self.cur[-1].t >= self.pause_s
            if too_long or (dur >= self.min_s and (pause or self._topic_shift(vec))):
                out.append(self.cur)
                self.cur, self._vecs = [], []
        self.cur.append(line)
        self._vecs.append(vec)
        return out

    def flush(self) -> list[list]:
        out = [self.cur] if self.cur else []
        self.cur, self._vecs = [], []
        return out
