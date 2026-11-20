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
_HYPH_WORD = re.compile(r"\w+(?:[-\u2010\u2011]\w+)*", re.UNICODE)  # #1562: "Кто-то" = one word
# #273 clause boundary for a timeout release in whole-sentence mode: after , ; : (Latin, then a space) or CJK ，；：、
_CLAUSE = re.compile(r"(?<=[,;:])\s+|(?<=[，；：、])\s*")
MIN_FINAL_UNITS = 4  # #288: a pause / timeout does not release a buffer shorter than this (a head, not a sentence)
WHOLE_PAUSE_UNITS = 8  # #288: whole mode (drafts cover the wait): a pause releases only a buffer at least this long
CLAUSE_MIN_UNITS = 3  # a clause shorter than this is not worth its own final line
FUZZY_MIN = 3  # #273: an overlap this long is dropped even with one garbled word at the seam
# #1395: a fragment (shorter than min_words) that arrives within AMEND_WINDOW seconds of the previous released line
# is joined to that line, which is then translated again and replaces the shown line ("... for the new." +
# "Version." -> "... for the new version."). 0 = off.
AMEND_WINDOW = 3.0
# #1395: one-word lines that are complete on their own are never joined to the previous line (compared in _norm form)
_STANDALONE = frozenset({
    "yes", "no", "ok", "okay", "thanks", "sure", "right", "yeah", "yep", "nope", "hello", "hi", "bye", "exactly",
    "great", "fine", "good", "well", "alright", "agreed", "correct", "true", "really", "wow", "please",
    "да", "нет", "спасибо", "хорошо", "ладно", "конечно", "понятно", "ага", "угу", "привет", "пока", "итак", "точно",
    "отлично", "верно", "правильно", "согласен", "согласна", "пожалуйста", "здравствуйте", "ясно", "так", "ну",
})
# #1562: a late fragment led by one of these words continues the previous sentence even when Whisper capitalised it
# after the pause ("... для новой." + "И версии." -> one line); compared in _norm form
_CONT_LEAD = frozenset({"and", "or", "и", "или"})
_Q_END = re.compile(r"[?!？！][\"'»”)\]]*\s*$")


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
    first: object = None  # #239: tag of the oldest segment whose text is in this unit (None: same as tag)
    # #1395: set when this unit is the previous line plus a late fragment: the text of the line it replaces
    replaces: str | None = None
    part: str = ""  # #1395: the late fragment alone (what to show when the caller cannot replace the line)


def _tail_join(prev: str, frag: str) -> str:
    """#1395: "... for the new." + "Version." -> "... for the new version.": the period Whisper put before the pause
    goes, and the capital it then gave the next word goes too (a lone "I" keeps its capital)."""
    p = prev.rstrip()
    if p.endswith(".") and not p.endswith("..") and not _CJK.search(frag[:1]):
        p = p[:-1].rstrip()
        w = frag.split(" ", 1)[0]
        if w != "I" and w[:1].isupper() and not w[1:2].isupper():  # #1562: one-letter "И" / "A" too
            frag = frag[:1].lower() + frag[1:]
    return _join(p, frag)


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
                 min_words: int = 0, pause_send: float | None = None, amend_window: float = AMEND_WINDOW):
        self.max_wait, self.max_words, self.min_overlap = max_wait, max_words, min_overlap
        # #21: pause (s) that releases an unfinished buffer; None = the language's MERGE_PAUSE_SEND / PAUSE_SEND.
        # Set from --pause-send / TLWU_PAUSE_SEND so the 0.8 -> 0.5 s change can be measured before it ships.
        self.pause_send = pause_send
        # #389: a finished sentence shorter than this ("Максима Чижова.") is a fragment: it is joined to the previous
        # sentence of the same release, else held (at most max_wait) and joined to the next one. 0 = off.
        self.min_words = min_words
        # #1395: seconds after a released line in which a fragment is joined to it (needs min_words > 0); 0 = off
        self.amend_window = amend_window
        # (text, lang, release time, audio end or None) of the newest released line
        self._last: tuple[str, str, float, float | None] | None = None
        # #1562: audio-clock (start, end) of the newest pushed segment and the audio start of the oldest buffered
        # text. The late-fragment window is measured on audio when the caller gives spans: the wall clock between two
        # ASR results also holds ASR / queue delay (a 1 s pause read as 9 s on a slow CPU runner).
        self._a_seg: tuple[float, float] | None = None  # of the newest segment whose text was buffered
        self._a_new: tuple[float, float] | None = None  # of the segment add() is pushing now
        self._a_clause: Unit | None = None  # #1562: the whole-mode clause unit cut last: its audio end is unknown
        self._a_since: float | None = None
        # whole (#273, live drafts on): the draft covers the wait, so a timeout / cap release ends at the last clause
        # boundary (, ; :) and keeps the rest buffered instead of cutting mid-clause
        self.whole = whole
        self.buf = ""
        self.lang = ""
        self.since: float | None = None  # time the oldest buffered text arrived
        self._cut = False  # previous segment was a forced mid-speech cut
        self._tail: list[str] = []  # last pushed words, for overlap de-duplication
        self.tag: object = None  # tag of the newest segment whose text is buffered (#323)
        self._marks: list[tuple[int, object]] = []  # #435: (offset in buf where a segment's text starts, its tag)

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
            self._marks = [m for m in self._marks if m[0] < len(self.buf)]
        return " ".join(text.split()[drop_new:])

    def add(self, text: str, lang: str, now: float, forced_cut: bool = False, pause: float = 0.0,
            tag: object = None, span: tuple[float, float] | None = None) -> list[Unit]:
        """Push one ASR segment; returns the units released now (see _add). #1395: a finished fragment that arrives
        within amend_window of the previous released line is joined to it at once (a unit with `replaces` set).
        span (#1562): the segment's (start, end) on the audio clock; when given, "within amend_window" is the audio
        pause between the line's end and the fragment's start, not the wall time between their ASR results."""
        since, a_since = self.since, self._a_since
        self._a_new = (float(span[0]), float(span[1])) if span is not None else None
        a_arr = (self._a_new[0] if self._a_new else None) if since is None else a_since
        out = self._post(self._add(text, lang, now, forced_cut, pause, tag), now, now if since is None else since,
                         a_arr)
        arrived = now if self.since is None else self.since
        if self.buf and self.min_words > 0 and _is_end(self.buf) and self._is_fragment(self.buf) \
                and self._amendable(self.buf, self.lang, arrived, self._a_since):
            a_arr = self._a_since
            out += self._post([self._take(forced=False)], now, arrived, a_arr)
        return out

    def due(self, now: float) -> list[Unit]:
        """Release the buffer when it is too old or too long (see _due); #1395 late fragments join the last line."""
        since, a_since = self.since, self._a_since
        return self._post(self._due(now), now, now if since is None else since, a_since)

    def quiet(self, seconds: float, now: float) -> list[Unit]:
        """#716 silence after the newest segment (see _quiet); #1395 late fragments join the last line."""
        since, a_since = self.since, self._a_since
        return self._post(self._quiet(seconds, now), now, now if since is None else since, a_since)

    def flush(self) -> list[Unit]:
        """End of input: release what is buffered; #1395 a late fragment still joins the previous line (its arrival
        time is known)."""
        since, a_since = self.since, self._a_since
        out = [self._take(forced=True)] if self.buf else []
        if out and since is not None:
            out = self._post(out, since, since, a_since)
        self._last = None
        return out

    def _separate(self, prev: str, frag: str) -> bool:
        """#1395: True if `frag` is a complete short line of its own and must not be joined to `prev`: a question or
        exclamation (either side), a one-word standalone reply ("Yes.", "Да."), or a capitalised clause of 2+ words
        after a finished sentence."""
        if _Q_END.search(frag) or _Q_END.search(prev):
            return True
        # #1562: a hyphenated word is one word here ("Кто-то." / Whisper's garbled "Идер-си." for "И версии"), so a
        # one-word late tail is not taken for a capitalised clause of its own
        words = _HYPH_WORD.findall(frag)
        if len(words) == 1 and _norm(words[0]) in _STANDALONE:
            return True
        if words and _norm(words[0]) in _CONT_LEAD:  # #1562: "And ..." / "И ..." continues the sentence
            return False
        return _is_end(prev) and frag.lstrip("\"'«“„([")[:1].isupper() and len(words) >= 2

    def _amendable(self, text: str, lang: str, arrived: float, a_arrived: float | None = None) -> bool:
        if self.min_words <= 0 or self.amend_window <= 0 or self._last is None or not text.strip():
            return False
        prev, plang, t, a_end = self._last
        if a_arrived is not None and a_end is not None:  # #1562: the pause in the audio, not the ASR wall time
            near = a_arrived - a_end <= self.amend_window
        else:
            near = 0.0 <= arrived - t <= self.amend_window
        return lang == plang and near and self._is_fragment(text) and not self._separate(prev, text.strip())

    def _post(self, units: list[Unit], now: float, arrived: float, a_arrived: float | None = None) -> list[Unit]:
        """#1395: turn a late fragment into "previous line + fragment" (replaces = the previous line's text) and
        remember the newest released line (#1562: with the audio end of the newest pushed segment, if known)."""
        out = []
        for u in units:
            clause = u is self._a_clause
            if clause:
                self._a_clause = None
            if self._amendable(u.text, u.lang, arrived, a_arrived):
                prev = self._last[0]
                u = Unit(_tail_join(prev, u.text), u.lang, u.forced, u.tag, u.first, replaces=prev, part=u.text)
            # a clause cut from the buffer ends somewhere before the newest segment's end: no audio end, so the next
            # fragment is judged on the wall clock as before #1562
            self._last = (u.text, u.lang, now, self._a_seg[1] if self._a_seg and not clause else None)
            out.append(u)
        return out

    def _add(self, text: str, lang: str, now: float, forced_cut: bool = False, pause: float = 0.0,
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
            return out + self._due(now)
        self._a_seg = self._a_new  # #1562: only a segment that adds text moves the buffered text's audio end
        self._tail = (self._tail + text.split())[-10:]
        self.tag = tag
        if not self.buf:
            self.since = now
            self._a_since = self._a_seg[0] if self._a_seg else None
        self.lang = lang
        self.buf = f"{self.buf} {text}".strip() if not _CJK.search(text[:1]) else self.buf + text
        self._marks.append((len(self.buf) - len(text), tag))
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
        out += [Unit(s.strip(), lang, False, tag, self._first() if i == 0 else tag) for i, s in enumerate(sents)]
        self._rebase(len(self.buf) - len(rest))
        self.buf = rest
        if not rest:
            self.since = self._a_since = None
        elif sents:
            self.since = now  # remainder starts a fresh wait
            self._a_since = self._a_seg[0] if self._a_seg else None
        send = self.pause_send if self.pause_send is not None else \
            lang_patches.setting(lang, "MERGE_PAUSE_SEND", PAUSE_SEND)
        if rest and not forced_cut and pause >= send \
                and _units(rest) >= (WHOLE_PAUSE_UNITS if self.whole else MIN_FINAL_UNITS):
            out.append(self._take(forced=True))  # speaker paused: no point waiting for max_wait
            return out
        return out + self._due(now)

    def _quiet(self, seconds: float, now: float) -> list[Unit]:
        """#716: the segmenter saw `seconds` of silence after the newest segment and no new speech. Release the
        buffer once that reaches the pause-send threshold: an unfinished tail after a forced cut, or a finished
        fragment held by min_words (#389), would otherwise wait the full max_wait for text that is not coming."""
        if not self.buf:
            return []
        send = self.pause_send if self.pause_send is not None else \
            lang_patches.setting(self.lang, "MERGE_PAUSE_SEND", PAUSE_SEND)
        if seconds < send:
            return self._due(now)
        held = any(_is_end(x) for x in _SPLIT.split(self.buf) if x.strip())  # a finished fragment is held
        if held or _units(self.buf) >= (WHOLE_PAUSE_UNITS if self.whole else MIN_FINAL_UNITS):
            return [self._take(forced=True)]
        return self._due(now)

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

    def _due(self, now: float) -> list[Unit]:
        """Release the buffer when it is too old or too long (whole mode: up to the last clause boundary)."""
        if not self.buf:
            return []
        # #288: a 1-3 word head ("Well, in the") waits up to twice as long for the rest of its sentence; a finished
        # fragment held by min_words (#389) never waits longer than max_wait
        held = any(_is_end(x) for x in _SPLIT.split(self.buf) if x.strip())  # a finished fragment is held
        head = self.whole and _units(self.buf) < MIN_FINAL_UNITS and not held
        wait = 2 * self.max_wait if head else self.max_wait
        if (self.since is not None and now - self.since >= wait) or _units(self.buf) >= self.max_words:
            if self.whole:
                head, rest = split_clause(self.buf)
                if head:
                    # #435: the clause carries the tag of the newest segment it holds text of, not the buffer's
                    tag, first = self._tag_before(len(head)), self._first()
                    self._rebase(len(self.buf) - len(rest))
                    self.buf, self.since = rest, (now if rest else None)
                    self._a_since = (self._a_seg[0] if self._a_seg else None) if rest else None
                    self._a_clause = Unit(head, self.lang, True, tag, first)
                    return [self._a_clause]
            return [self._take(forced=True)]
        return []

    def _first(self) -> object:
        """Tag of the segment holding the start of the buffer (a stale mark of released text also sits at 0)."""
        return self._tag_before(1)

    def _tag_before(self, end: int) -> object:
        """Tag of the newest segment whose text starts before offset `end` of the buffer."""
        tags = [t for off, t in self._marks if off < end]
        return tags[-1] if tags else self.tag

    def _rebase(self, start: int) -> None:
        """The buffer now begins at old offset `start`: shift the marks; the segment holding that offset starts at 0."""
        kept = [(off - start, t) for off, t in self._marks if off > start]
        older = [t for off, t in self._marks if off <= start]
        self._marks = ([(0, older[-1])] if older else []) + kept

    def _take(self, forced: bool) -> Unit:
        u = Unit(self.buf.strip(), self.lang, forced, self.tag, self._first())
        self.buf, self.since, self._a_since = "", None, None
        self._marks = []
        return u
