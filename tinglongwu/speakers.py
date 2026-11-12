"""Online speaker clustering (pure logic, no IO / model / network).

Feed one sentence embedding + its duration; get back a speaker label
("說話者1", "說話者2", ...) or "?" when it cannot be decided yet.
"""
from __future__ import annotations

import math
from typing import Sequence

UNKNOWN = "?"
LABEL_PREFIX = "說話者"


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity; 0.0 if either vector has zero length/norm."""
    if len(a) != len(b):
        raise ValueError("embedding dimension mismatch")
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


class SpeakerClusterer:
    """Online clustering by cosine similarity to running cluster centroids.

    threshold:    cosine >= threshold means "same speaker".
    min_seconds:  sentences shorter than this never create/update a cluster;
                  they reuse the previous speaker, or "?" if there is none.
    """

    def __init__(self, threshold: float = 0.6, min_seconds: float = 1.0):
        self.threshold = threshold
        self.min_seconds = min_seconds
        self._sums: list[list[float]] = []  # per-cluster sum of unit vectors
        self._last: str = UNKNOWN

    @property
    def count(self) -> int:
        return len(self._sums)

    def assign(self, embedding: Sequence[float], seconds: float) -> str:
        if seconds < self.min_seconds:
            return self._last
        norm = math.sqrt(sum(x * x for x in embedding))
        if norm == 0.0:
            return self._last
        unit = [x / norm for x in embedding]
        best, best_sim = -1, -2.0
        for i, s in enumerate(self._sums):
            sim = cosine(unit, s)
            if sim > best_sim:
                best, best_sim = i, sim
        if best >= 0 and best_sim >= self.threshold:
            self._sums[best] = [a + b for a, b in zip(self._sums[best], unit)]
        else:
            self._sums.append(unit)
            best = len(self._sums) - 1
        self._last = f"{LABEL_PREFIX}{best + 1}"
        return self._last
