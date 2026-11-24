"""Target-script check for one translated line (#1819, port of mobile/lib/logic/target_script.dart, #1616).

A line is in the wrong script for the target language when it has no letter of the target script and at least
`min_foreign` letters of another script that were not copied from the source (a name or "OK" kept as written
is fine). Unknown targets and empty output never count as wrong. Shared golden cases: shared/golden/script.jsonl.
"""
from __future__ import annotations

import re
import unicodedata


def _cyrillic(c: int) -> bool:
    return 0x0400 <= c <= 0x052F


def _han(c: int) -> bool:
    return 0x3400 <= c <= 0x4DBF or 0x4E00 <= c <= 0x9FFF or 0xF900 <= c <= 0xFAFF


def _kana(c: int) -> bool:
    return 0x3040 <= c <= 0x30FF


def _hangul(c: int) -> bool:
    return 0xAC00 <= c <= 0xD7AF or 0x1100 <= c <= 0x11FF or 0x3130 <= c <= 0x318F


def _thai(c: int) -> bool:
    return 0x0E00 <= c <= 0x0E7F


def _latin(c: int) -> bool:
    return (0x41 <= c <= 0x5A or 0x61 <= c <= 0x7A or (0xC0 <= c <= 0x24F and c not in (0xD7, 0xF7))
            or 0x1E00 <= c <= 0x1EFF)


_TARGET_SCRIPTS = {
    "zh": (_han,), "yue": (_han,), "ja": (_kana, _han), "ko": (_hangul,), "ru": (_cyrillic,), "th": (_thai,),
    **{lang: (_latin,) for lang in ("en", "es", "fr", "de", "it", "pt", "vi", "id")},
}
_LATIN_WORD = re.compile("[A-Za-zÀ-ɏḀ-ỿ]+")


def wrong_script(src: str, out: str, tgt: str, min_foreign: int = 2) -> bool:
    scripts = _TARGET_SCRIPTS.get(tgt)
    if scripts is None or not out.strip():
        return False
    if any(s(ord(ch)) for ch in out for s in scripts):
        return False
    src_words = {m.group(0) for m in _LATIN_WORD.finditer(src.lower())}
    kept = _LATIN_WORD.sub(lambda m: "" if m.group(0).lower() in src_words else m.group(0), out)
    src_chars = set(src)
    foreign = 0
    for ch in kept:
        if not _latin(ord(ch)) and ch in src_chars:
            continue
        if unicodedata.category(ch).startswith("L"):
            foreign += 1
    return foreign >= min_foreign
