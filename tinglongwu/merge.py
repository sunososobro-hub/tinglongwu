"""Sentence merging between ASR and translation.

VAD / forced cuts split speech mid-sentence ("... новой зеландии я" | "думаю ..."), and MT quality
suffers from half sentences. SentenceMerger buffers ASR text and releases it at sentence boundaries,
or when a latency cap is hit (concept borrowed from whisper_streaming: commit only at sentence ends,
bounded by a timeout; no re-decoding of audio, so no extra ASR cost).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import lang as lang_patches

# split after terminal punctuation: followed by whitespace (Latin/Cyrillic) or directly (CJK full-width)
_SPLIT = re.compile(r"(?<=[.!?…])\s+|(?<=[。！？])\s*")
_END = re.compile(r"[.!?…。！？][\"'»”)\]]*\s*$")
_ABBREV = re.compile(r"(?:^|\s)(?:\w{1,2}\.)$")  # "т.", "г.", "Mr." do not end a sentence
# #288: lowercase 1-2 letter ASCII words ("up.", "it.", "so.") are ordinary words that end a sentence; only these
# lowercase ones are real abbreviations ("vs.", "cf.", "ca.", "no." is not: "I said no." ends a sentence)
_ABBREV_LOWER_ASCII = frozenset({"vs", "cf", "ca", "pp", "ed", "al"})
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
_TRAIL_ELLIPSIS = re.compile(r"\s*(?:\.{2,}|…)+\s*$")
PAUSE_SEND = 0.8  # seconds of VAD silence after a segment that means "the speaker stopped": send what we have
_WORD = re.compile(r"\w+", re.UNICODE)
# #273 clause boundary for a timeout release in whole-sentence mode: after , ; : (Latin, then a space) or CJK ，；：、
_CLAUSE = re.compile(r"(?<=[,;:])\s+|(?<=[，；：、])\s*")
MIN_FINAL_UNITS = 4  # #288: a pause / timeout does not release a buffer shorter than this (a head, not a sentence)
WHOLE_PAUSE_UNITS = 8  # #288: whole mode (drafts cover the wait): a pause releases only a buffer at least this long
CLAUSE_MIN_UNITS = 3  # a clause shorter than this is not worth its own final line
FUZZY_MIN = 3  # #273: an overlap this long is dropped even with one garbled word at the seam


def _join(a: str, b: str) -> str:
    """Concatenate two pieces of text: no space before CJK text, one space otherwise."""
    return a + b if _CJK.search(b[:1]) else f"{a} {b}"


def _units(text: str) -> int:
    """Length for the cap: words, with every CJK character counting as a word."""
    return len(_WORD.sub("x", _CJK.sub(" x ", text)).split())


def _norm(w: str) -> str:
    return "".join(c for c in w.lower().replace("ё", "е") if c.isalnum())


def dedupe_overlap(tail: list[str], text: str, min_overlap: int = 2) -> str:
    """Drop words at the start of `text` that repeat the end of `tail` (the previous words; VAD / forced-cut audio
    overlap makes Whisper say them twice). Exact match of >= min_overlap words; or (#273) a run of >= FUZZY_MIN
    words when one garbled word sits at the seam: a cut-off word at the end of `tail` ("... and mathemat") or a
    misheard word at the start of `text` ("planes linguistic and mathematical" after "... linguistic and
    mathematical")."""
    return " ".join(text.split()[_overlap(tail, text, min_overlap)[1]:]) if text.strip() else text


def _overlap(tail: list[str], text: str, min_overlap: int) -> tuple[int, int]:
    """-> (garbled words to drop from the end of tail: 0 or 1, words to drop from the start of text)."""
    new = [_norm(w) for w in text.split()]
    old = [_norm(w) for w in tail]
    for k in range(min(len(old), len(new), 8), max(1, min_overlap) - 1, -1):
        if old[-k:] == new[:k] and all(new[:k]):
            return 0, k
    for k in range(min(len(old), len(new), 8), FUZZY_MIN - 1, -1):
        for skip_old, skip_new in ((1, 0), (0, 1), (1, 1)):
            a = old[len(old) - k - skip_old: len(old) - skip_old]
            if len(a) == k and a == new[skip_new: skip_new + k] and all(a):
                return skip_old, skip_new + k
    return 0, 0


def _is_end(text: str) -> bool:
    t = text.rstrip()
    if not _END.search(t):
        return False
    core = re.sub(r"[\"'»”)\]]+$", "", t)
    m = _ABBREV.search(core)
    if not m:
        return True
    word = m.group(0).strip()[:-1]
    # #288: "it." / "up." (ASCII, lowercase, 2 letters, not a known abbreviation) is a normal sentence end
    return len(word) == 2 and word.isascii() and word.islower() and word not in _ABBREV_LOWER_ASCII


@dataclass
class Unit:
    text: str
    lang: str
    forced: bool  # released by the cap / timeout / end of stream rather than a sentence boundary
    tag: object = None  # #323: add()'s tag of the newest segment whose text is in this unit (latency marks)


def split_clause(text: str) -> tuple[str, str]:
    """(head up to the last clause boundary, rest) or ("", text) when there is none (or the head would be shorter
    than CLAUSE_MIN_UNITS)."""
    cuts = [m for m in _CLAUSE.finditer(text) if _units(text[:m.start()]) >= CLAUSE_MIN_UNITS]
    if not cuts:
        return "", text
    m = cuts[-1]
    return text[:m.start()].strip(), text[m.end():].strip()


class SentenceMerger:
    """Push ASR segments, get translation units. Time is injected (seconds, any monotonic clock)."""

    def __init__(self, max_wait: float = 1.5, max_words: int = 45, min_overlap: int = 2, whole: bool = False,
                 min_words: int = 0):
        self.max_wait, self.max_words, self.min_overlap = max_wait, max_words, min_overlap
        # #389: a finished sentence shorter than this ("Максима Чижова.") is a fragment: it is joined to the previous
        # sentence of the same release, else held (at most max_wait) and joined to the next one. 0 = off.
        self.min_words = min_words
        # whole (#273, live drafts on): the draft covers the wait, so a timeout / cap release ends at the last clause
        # boundary (, ; :) and keeps the rest buffered instead of cutting mid-clause
        self.whole = whole
        self.buf = ""
        self.lang = ""
        self.since: float | None = None  # time the oldest buffered text arrived
        self._cut = False  # previous segment was a forced mid-speech cut
        self._tail: list[str] = []  # last pushed words, for overlap de-duplication
        self.tag: object = None  # tag of the newest segment whose text is buffered (#323)

    @property
    def deadline(self) -> float | None:
        return None if self.since is None else self.since + self.max_wait

    def _dedupe(self, text: str, min_overlap: int | None = None) -> str:
        """Drop words at the start of `text` that repeat the end of the previous segment (VAD overlap); a garbled
        cut-off word at the end of the still-held buffer is dropped too (the new segment has it whole)."""
        drop_old, drop_new = _overlap(self._tail, text, self.min_overlap if min_overlap is None else min_overlap)
        if drop_old and self._tail and self.buf.endswith(self._tail[-1]):
            self.buf = self.buf[:-len(self._tail[-1])].rstrip()
            self._tail = self._tail[:-1]
        return " ".join(text.split()[drop_new:])

    def add(self, text: str, lang: str, now: float, forced_cut: bool = False, pause: float = 0.0,
            tag: object = None) -> list[Unit]:
        """forced_cut: the segmenter cut mid-speech (chunk limit), not at a VAD pause, so a trailing
        "..." from Whisper is only a cut artifact and not a sentence end. pause: seconds of silence that
        followed the segment (>= PAUSE_SEND, or the language's MERGE_PAUSE_SEND from tinglongwu/lang/, releases an
        unfinished buffer at once). tag: opaque, carried to every released unit that holds text of this segment."""
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
        self._tail = (self._tail + text.split())[-10:]
        self.tag = tag
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
        if self.min_words > 0:
            sents, rest = self._merge_fragments(sents, rest)
        out += [Unit(s.strip(), lang, False, tag) for s in sents]
        self.buf = rest
        if not rest:
            self.since = None
        elif sents:
            self.since = now  # remainder starts a fresh wait
        if rest and not forced_cut and pause >= lang_patches.setting(lang, "MERGE_PAUSE_SEND", PAUSE_SEND) \
                and _units(rest) >= (WHOLE_PAUSE_UNITS if self.whole else MIN_FINAL_UNITS):
            out.append(self._take(forced=True))  # speaker paused: no point waiting for max_wait
            return out
        return out + self.due(now)

    def _is_fragment(self, text: str) -> bool:
        # words only (_units also counts a lone CJK punctuation mark like "。" as one): each CJK character is a word
        return len(_WORD.findall(_CJK.sub(" x ", text))) < self.min_words

    def _merge_fragments(self, sents: list[str], rest: str) -> tuple[list[str], str]:
        """#389: join each fragment sentence to its neighbour: to the next finished sentence, else to the previous
        one; a fragment with no finished neighbour goes back into the buffer to wait for the next text."""
        merged: list[str] = []
        for s in sents:
            if merged and (self._is_fragment(merged[-1]) or self._is_fragment(s)):
                merged[-1] = _join(merged[-1], s)
            else:
                merged.append(s)
        if merged and self._is_fragment(merged[-1]):  # only possible when it is the sole sentence: hold it
            rest = _join(merged.pop(), rest) if rest else merged.pop()
        return merged, rest

    def due(self, now: float) -> list[Unit]:
        """Release the buffer when it is too old or too long (whole mode: up to the last clause boundary)."""
        if not self.buf:
            return []
        # #288: a 1-3 word head ("Well, in the") waits up to twice as long for the rest of its sentence; a finished
        # fragment held by min_words (#389) never waits longer than max_wait
        head = self.whole and _units(self.buf) < MIN_FINAL_UNITS \n            and not any(_is_end(x) for x in _SPLIT.split(self.buf) if x.strip())
        wait = 2 * self.max_wait if head else self.max_wait
        if (self.since is not None and now - self.since >= wait) or _units(self.buf) >= self.max_words:
            if self.whole:
                head, rest = split_clause(self.buf)
                if head:
                    self.buf, self.since = rest, (now if rest else None)
                    return [Unit(head, self.lang, True, self.tag)]
            return [self._take(forced=True)]
        return []

    def flush(self) -> list[Unit]:
        return [self._take(forced=True)] if self.buf else []

    def _take(self, forced: bool) -> Unit:
        u = Unit(self.buf.strip(), self.lang, forced, self.tag)
        self.buf, self.since = "", None
        return u
