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


class SpeakerBook:
    """#384: session-only speaker names and corrections (nothing is persisted; no voiceprint is kept).

    add() one line's auto label (None if unknown) per final line, in order. Names and merges apply to past and
    future lines through display(); relabel() changes one line only. apply() replays a saved correction event."""

    def __init__(self):
        self.names: dict[str, str] = {}  # label -> user-given name
        self.alias: dict[str, str] = {}  # merged label -> the label it was merged into
        self.lines: list[list] = []  # [auto label, corrected label] per line

    def add(self, label: str | None) -> int:
        self.lines.append([label, label])
        return len(self.lines) - 1

    def resolve(self, label: str | None) -> str | None:
        while label in self.alias:  # merge() only links roots to other roots: no cycles
            label = self.alias[label]
        return label

    def display(self, label: str | None) -> str | None:
        label = self.resolve(label)
        return self.names.get(label, label)

    def new_label(self) -> str:
        used = {lab for line in self.lines for lab in line if lab} | set(self.alias)
        n = 1
        while f"{LABEL_PREFIX}{n}" in used:
            n += 1
        return f"{LABEL_PREFIX}{n}"

    def rename(self, label: str, name: str) -> None:
        label = self.resolve(label)
        if name.strip():
            self.names[label] = name.strip()
        else:
            self.names.pop(label, None)

    def relabel(self, index: int, label: str) -> None:
        self.lines[index][1] = label

    def merge(self, src: str, dst: str) -> None:
        src, dst = self.resolve(src), self.resolve(dst)
        if src != dst:
            self.alias[src] = dst
            name = self.names.pop(src, None)
            if name and dst not in self.names:  # keep a name the user gave only to the merged-away speaker
                self.names[dst] = name

    def corrected(self, index: int) -> str | None:
        return self.resolve(self.lines[index][1])

    def apply(self, event: dict) -> None:
        """{"rename": label, "name": str} | {"relabel": index, "speaker": label} | {"merge": label, "into": label}"""
        if "rename" in event:
            self.rename(event["rename"], event.get("name", ""))
        elif "relabel" in event:
            self.relabel(int(event["relabel"]), event["speaker"])
        elif "merge" in event:
            self.merge(event["merge"], event["into"])
        else:
            raise ValueError(f"unknown speaker correction: {event!r}")
