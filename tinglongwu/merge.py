"""Sentence merging between ASR and translation.

VAD / forced cuts split speech mid-sentence ("... новой зеландии я" | "думаю ..."), and MT quality
suffers from half sentences. SentenceMerger buffers ASR text and releases it at sentence boundaries,
or when a latency cap is hit (concept borrowed from whisper_streaming: commit only at sentence ends,
bounded by a timeout; no re-decoding of audio, so no extra ASR cost).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# split after terminal punctuation: followed by whitespace (Latin/Cyrillic) or directly (CJK full-width)
_SPLIT = re.compile(r"(?<=[.!?…])\s+|(?<=[。！？])\s*")
_END = re.compile(r"[.!?…。！？][\"'»”)\]]*\s*$")
_ABBREV = re.compile(r"(?:^|\s)(?:\w{1,2}\.)$")  # "т.", "г.", "Mr." do not end a sentence
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
_TRAIL_ELLIPSIS = re.compile(r"\s*(?:\.{2,}|…)+\s*$")
PAUSE_SEND = 0.8  # seconds of VAD silence after a segment that means "the speaker stopped": send what we have
_WORD = re.compile(r"\w+", re.UNICODE)


def _units(text: str) -> int:
    """Length for the cap: words, with every CJK character counting as a word."""
    return len(_WORD.sub("x", _CJK.sub(" x ", text)).split())


def _norm(w: str) -> str:
    return "".join(c for c in w.lower().replace("ё", "е") if c.isalnum())


def _is_end(text: str) -> bool:
    t = text.rstrip()
    return bool(_END.search(t)) and not _ABBREV.search(re.sub(r"[\"'»”)\]]+$", "", t))


@dataclass
class Unit:
    text: str
    lang: str
    forced: bool  # released by the cap / timeout / end of stream rather than a sentence boundary


class SentenceMerger:
    """Push ASR segments, get translation units. Time is injected (seconds, any monotonic clock)."""

    def __init__(self, max_wait: float = 1.5, max_words: int = 45, min_overlap: int = 2):
        self.max_wait, self.max_words, self.min_overlap = max_wait, max_words, min_overlap
        self.buf = ""
        self.lang = ""
        self.since: float | None = None  # time the oldest buffered text arrived
        self._cut = False  # previous segment was a forced mid-speech cut
        self._tail: list[str] = []  # last pushed words, for overlap de-duplication

    @property
    def deadline(self) -> float | None:
        return None if self.since is None else self.since + self.max_wait

    def _dedupe(self, text: str, min_overlap: int | None = None) -> str:
        """Drop words at the start of `text` that repeat the end of the previous segment (VAD overlap)."""
        words = text.split()
        n_new = [_norm(w) for w in words]
        lo = (self.min_overlap if min_overlap is None else min_overlap) - 1
        for k in range(min(len(self._tail), len(words), 6), lo, -1):
            if [_norm(w) for w in self._tail[-k:]] == n_new[:k] and all(n_new[:k]):
                return " ".join(words[k:])
        return text

    def add(self, text: str, lang: str, now: float, forced_cut: bool = False, pause: float = 0.0) -> list[Unit]:
        """forced_cut: the segmenter cut mid-speech (chunk limit), not at a VAD pause, so a trailing
        "..." from Whisper is only a cut artifact and not a sentence end. pause: seconds of silence that
        followed the segment (>= PAUSE_SEND releases an unfinished buffer at once)."""
        out: list[Unit] = []
        if self.buf and lang != self.lang:
            out.append(self._take(forced=True))
        text = text.strip()
        if forced_cut:
            text = _TRAIL_ELLIPSIS.sub("", text)
        # one repeated word is enough to dedupe right after a cut segment ("австралийскую..." | "австралийскую визу")
        text = self._dedupe(text, 1 if self._cut else None)
        self._cut = forced_cut
        if not text:
            return out + self.due(now)
        self._tail = (self._tail + text.split())[-8:]
        if not self.buf:
            self.since = now
        self.lang = lang
        self.buf = f"{self.buf} {text}".strip() if not _CJK.search(text[:1]) else self.buf + text
        parts = [p for p in _SPLIT.split(self.buf) if p.strip()]
        # keep splitting only where the sentence really ends (abbreviation guard)
        sents: list[str] = []
        for p in parts:
            if sents and not _is_end(sents[-1]):
                sents[-1] = f"{sents[-1]} {p}"
            else:
                sents.append(p)
        rest = ""
        if sents and not _is_end(sents[-1]):
            rest = sents.pop()
        out += [Unit(s.strip(), lang, False) for s in sents]
        self.buf = rest
        if not rest:
            self.since = None
        elif sents:
            self.since = now  # remainder starts a fresh wait
        if rest and not forced_cut and pause >= PAUSE_SEND:
            out.append(self._take(forced=True))  # speaker paused: no point waiting for max_wait
            return out
        return out + self.due(now)

    def due(self, now: float) -> list[Unit]:
        """Release the buffer when it is too old or too long."""
        if self.buf and ((self.since is not None and now - self.since >= self.max_wait)
                         or _units(self.buf) >= self.max_words):
            return [self._take(forced=True)]
        return []

    def flush(self) -> list[Unit]:
        return [self._take(forced=True)] if self.buf else []

    def _take(self, forced: bool) -> Unit:
        u = Unit(self.buf.strip(), self.lang, forced)
        self.buf, self.since = "", None
        return u
