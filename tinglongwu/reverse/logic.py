"""Pure helpers (no GPU, no audio hardware): sentence splitting, output-device matching, resampling."""
from __future__ import annotations

import re
from math import gcd

import numpy as np

_END = re.compile(r"(?<=[.!?…])\s+|(?<=[。！？])")


def split_sentences(text: str, max_chars: int = 220) -> list[str]:
    """Split translated text into sentences for the TTS; over-long pieces are cut at commas/spaces."""
    out: list[str] = []
    for part in _END.split(text.strip()):
        part = part.strip()
        while len(part) > max_chars:
            cut = max(part.rfind(c, 0, max_chars) for c in (",", ";", ":", " "))
            cut = cut if cut > 0 else max_chars
            out.append(part[:cut].strip(" ,;:"))
            part = part[cut:].strip(" ,;:")
        if part:
            out.append(part)
    return [s for s in out if re.search(r"\w", s)]


def match_device(names: list[str], needle: str | None) -> int | None:
    """Index of the first device whose name contains `needle` (case-insensitive); no needle -> None (default)."""
    if not needle:
        return None
    n = needle.casefold()
    for i, name in enumerate(names):
        if n in name.casefold():
            return i
    raise LookupError(f"no output device matching {needle!r}; available: {names}")


def resample(audio: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """Polyphase resample of a mono float array; returns float32."""
    audio = np.asarray(audio, dtype=np.float32)
    if sr_in == sr_out or audio.size == 0:
        return audio
    from scipy.signal import resample_poly

    g = gcd(sr_in, sr_out)
    return resample_poly(audio, sr_out // g, sr_in // g).astype(np.float32)


_CJK = re.compile(r"[⺀-鿿　-〿豈-﫿＀-￯]")
RETRY_HINTS = {"ru": "\n(Reply in Russian only. Do not output any Chinese characters.)",
               "en": "\n(Reply in English only. Do not output any Chinese characters.)"}
RETRY_HINT = RETRY_HINTS["ru"]


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text))


def strip_cjk(text: str) -> str:
    """Last resort: drop CJK characters and tidy the leftover punctuation/spaces."""
    t = _CJK.sub(" ", text)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    return re.sub(r"\s{2,}", " ", t).strip(" ,;")


def translate_ru(translate, zh: str, lang: str = "ru") -> tuple[str, int]:
    """translate(text)->str. Returns (translation, retries): one retry with a stricter hint when CJK leaks, then strip.
    `lang` (ru|en) only picks the hint wording; the function name is historical."""
    ru = translate(zh)
    if not has_cjk(ru):
        return ru, 0
    ru2 = translate(zh + RETRY_HINTS.get(lang, RETRY_HINT))
    return (ru2 if not has_cjk(ru2) else strip_cjk(ru2 or ru)), 1
