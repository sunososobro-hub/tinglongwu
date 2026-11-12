"""Repetition-loop handling for ASR text ("A B C A B C A B C ..." from a stuck Whisper decoder).

faster-whisper / openai-whisper only react to a loop through temperature fallback (re-decode at a higher
temperature when the compression ratio is > 2.4); with a single temperature there is no fallback and the
looped text is returned as is. Here: `has_loop` tells the recognizer to re-decode, `collapse_repeats` is
the safety net that keeps one copy of whatever is still repeated (also used before translation).
"""
from __future__ import annotations

import re
import zlib

MAX_UNIT_WORDS = 40
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
_CHAR_LOOP = re.compile(r"(.{3,80}?)\1{2,}", re.S)
# non-CJK char-level loop: one >= 2 char unit joined by "-" / spaces, 4+ copies ("по-по-по-по", "la-la-la-la")
MIN_UNIT_CHARS = 2
MIN_UNIT_REPEATS = 4
_UNIT_LOOP = re.compile(r"(?<!\w)(\w{%d,20}?)(?:[-\u2013\s]+\1){%d,}(?!\w)" % (MIN_UNIT_CHARS, MIN_UNIT_REPEATS - 1),
                        re.I)
MAX_COMPRESSION_RATIO = 2.4  # Whisper's own compression-ratio limit for a decoder stuck in a loop
MIN_RATIO_BYTES = 32  # below this the ratio is meaningless (zlib header overhead)
MIN_KEEP_CHARS = 2  # a collapsed result with fewer letters/digits than this is noise


def _norm(w: str) -> str:
    return "".join(c for c in w.lower().replace("ё", "е") if c.isalnum()) or w.lower()


def _collapse_words(text: str) -> str:
    words = text.split()
    n = [_norm(w) for w in words]
    out: list[str] = []
    i = 0
    while i < len(words):
        hit = None
        for L in range(1, min(MAX_UNIT_WORDS, (len(words) - i) // 2) + 1):
            unit = n[i:i + L]
            k = 1
            while n[i + k * L:i + (k + 1) * L] == unit:
                k += 1
            if k >= (4 if L == 1 else 3):  # "да да да" is speech, 4+ identical words is a loop
                hit = (L, k)
                break
        if hit is None:
            out.append(words[i])
            i += 1
            continue
        L, k = hit
        out.extend(words[i:i + L])
        j = i + k * L
        m = 0  # trailing partial copy of the unit (decoder cut mid-loop)
        while m < L and j + m < len(words) and n[j + m] == n[i + m]:
            m += 1
        i = j + m
    return " ".join(out)


def _collapse_chars(text: str) -> str:
    return _CHAR_LOOP.sub(lambda m: m.group(1) if _CJK.search(m.group(1)) else m.group(0), text)


def collapse_repeats(text: str) -> str:
    """Keep one copy of any phrase repeated >= 3 times in a row (>= 4 for a single word)."""
    t = " ".join(text.split())
    if not t:
        return t
    t = _UNIT_LOOP.sub(lambda m: m.group(1), t)
    return _collapse_chars(_collapse_words(t))


def compression_ratio(text: str) -> float:
    b = text.encode("utf-8")
    return len(b) / max(1, len(zlib.compress(b)))


def filter_hallucination(text: str) -> str:
    """Collapse repeats; return "" (drop the sentence) when what is left is still a loop or too short."""
    t = collapse_repeats(text)
    if not t:
        return t
    if t != " ".join(text.split()) and sum(c.isalnum() for c in t) < MIN_KEEP_CHARS:
        return ""
    if len(t.encode("utf-8")) >= MIN_RATIO_BYTES and compression_ratio(t) > MAX_COMPRESSION_RATIO:
        return ""
    return t


def has_loop(text: str) -> bool:
    t = " ".join(text.split())
    return len(collapse_repeats(t)) < 0.7 * len(t)
