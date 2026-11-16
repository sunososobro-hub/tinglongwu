"""Online speaker clustering (pure logic, no IO / model / network).

Feed one sentence embedding + its duration; get back a speaker label
("說話者1", "說話者2", ...) or "?" when it cannot be decided yet.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

UNKNOWN = "?"
LABEL_PREFIX = "說話者"
# #1256 numbers, measured with the shipped CAM++ model on 240 VCTK-voice sentences from 12 speakers (PR for #1256):
# 1-2 s sentences sit at cosine p10 0.73 to their own speaker but up to 0.64 to another speaker; from 2 s on, own >= 0.83.
# Speaker centroids reach at most 0.633 to each other, so 0.7 merges only clusters closer than any two real speakers.
SHORT_SECONDS = 2.0
SHORT_THRESHOLD = 0.5
MERGE_THRESHOLD = 0.7


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
    #1256 (one person split into many labels after short pauses):
    short_seconds / short_threshold: a sentence shorter than short_seconds has a noisy embedding, so it joins its
                  best cluster at the lower short_threshold instead of opening a new speaker.
    merge_threshold: after a cluster is updated, any other cluster whose centroid is at least this similar is merged
                  into the older one; later lines of either get the older label (merged maps old label -> kept one).
    """

    def __init__(self, threshold: float = 0.6, min_seconds: float = 1.0, short_seconds: float = SHORT_SECONDS,
                 short_threshold: float = SHORT_THRESHOLD, merge_threshold: float = MERGE_THRESHOLD):
        self.threshold = threshold
        self.min_seconds = min_seconds
        self.short_seconds = short_seconds
        self.short_threshold = min(short_threshold, threshold)
        self.merge_threshold = merge_threshold
        self._sums: list[list[float] | None] = []  # per-cluster sum of unit vectors; None = merged away
        self._into: dict[int, int] = {}  # merged cluster index -> the index it was merged into
        self.merged: dict[str, str] = {}  # #1256: merged label -> kept label (callers may alias past lines)
        self._edges: list[tuple[str, str]] = []  # #1256: (retired label, kept label) per merge, in merge order
        self._last: str = UNKNOWN
        import threading

        self._lock = threading.Lock()  # one clusterer is shared by successive labelers (live toggle)

    @property
    def count(self) -> int:
        """Distinct speakers now (clusters merged into another are not counted)."""
        return sum(1 for s in self._sums if s is not None)

    def assign(self, embedding: Sequence[float], seconds: float) -> str:
        with self._lock:
            return self._assign(embedding, seconds)

    def kept(self, label: str | None) -> str | None:
        """#1256: the label a merged speaker now has (any thread; the GUI resolves past lines through it)."""
        with self._lock:
            return self.merged.get(label, label) if label else label

    def merge_edges(self) -> list[tuple[str, str]]:
        """#1256: the merges in the order they happened, as (retired label, kept label) (any thread). Replaying them in
        order through SpeakerBook.merge keeps names along a chain (3 -> 2, then 2 -> 1)."""
        with self._lock:
            return list(self._edges)

    def merges(self) -> dict[str, str]:
        """#1256: a snapshot of merged label -> kept label (any thread)."""
        with self._lock:
            return dict(self.merged)

    def _label(self, i: int) -> str:
        return f"{LABEL_PREFIX}{i + 1}"

    def _assign(self, embedding: Sequence[float], seconds: float) -> str:
        if seconds < self.min_seconds:
            return self._last
        norm = math.sqrt(sum(x * x for x in embedding))
        if norm == 0.0:
            return self._last
        unit = [x / norm for x in embedding]
        best, best_sim = -1, -2.0
        for i, s in enumerate(self._sums):
            if s is None:
                continue
            sim = cosine(unit, s)
            if sim > best_sim:
                best, best_sim = i, sim
        need = self.short_threshold if seconds < self.short_seconds else self.threshold
        if best >= 0 and best_sim >= need:
            self._sums[best] = [a + b for a, b in zip(self._sums[best], unit)]
            best = self._merge_near(best)
        else:
            self._sums.append(unit)
            best = len(self._sums) - 1
        self._last = self._label(best)
        return self._last

    def _merge_near(self, i: int) -> int:
        """Merge every live cluster whose centroid is >= merge_threshold similar to cluster i; returns the kept index
        (the oldest of the merged ones, so the label a person had first is the one kept)."""
        while True:
            s = self._sums[i]
            near = [j for j, t in enumerate(self._sums)
                    if j != i and t is not None and cosine(s, t) >= self.merge_threshold]
            if not near:
                return i
            j = near[0]
            keep, gone = min(i, j), max(i, j)
            self._sums[keep] = [a + b for a, b in zip(self._sums[keep], self._sums[gone])]
            self._sums[gone] = None
            self._into[gone] = keep
            for k, v in list(self.merged.items()):
                if v == self._label(gone):
                    self.merged[k] = self._label(keep)
            self.merged[self._label(gone)] = self._label(keep)
            self._edges.append((self._label(gone), self._label(keep)))
            i = keep


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
