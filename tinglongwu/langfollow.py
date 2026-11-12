"""Per-segment language following for meeting mode.

The listen process detects the language of every segment among a small candidate set and records the last
spoken (non-Chinese) language in a tiny state file; the speak process (reverse.proto --to follow) reads it
before each sentence. Pure helpers, no models."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

DEFAULT_CANDIDATES = ("en", "ru", "ja", "zh")
SPEAK_LANGS = ("en", "ru", "ja")  # languages the speak side can voice; zh segments never update the state file


_KANA = re.compile("[\u3041-\u3096\u30a1-\u30fa]")  # hiragana + katakana letters (not the ・ ー marks)


def has_kana(text: str) -> bool:
    return bool(_KANA.search(text or ""))


def script_lang(text: str, lang: str) -> str:
    """Whisper labels kanji-heavy Japanese as zh; text with kana is never Chinese (#86): the script wins."""
    return "ja" if lang == "zh" and has_kana(text) else lang


def parse_candidates(s: str | None) -> tuple[str, ...]:
    out = tuple(dict.fromkeys(x.strip().lower() for x in (s or "").split(",") if x.strip()))
    return out or DEFAULT_CANDIDATES


SWITCH_MARGIN = 0.2   # new language must beat the current one by this much probability to take over
MIN_SWITCH_SECS = 1.5  # ...and the segment must be at least this long (short clips / noise do not flip the language)


def pick_candidate(probs, candidates, fallback: str | None = None, current: str | None = None,
                   seconds: float | None = None, margin: float = SWITCH_MARGIN,
                   min_secs: float = MIN_SWITCH_SECS) -> str:
    """Most probable language among `candidates`, with stickiness. probs: iterable of (lang, prob) or a dict.

    With `current` (the previous segment's language, if it is a candidate) the winner only replaces it when
    it is `margin` more probable and the segment lasts `seconds` >= `min_secs`; otherwise `current` is kept.
    No `current` (first segment) or no `seconds`: plain argmax over the candidates."""
    items = probs.items() if hasattr(probs, "items") else probs
    p = {lang: prob for lang, prob in items}
    best = max(candidates, key=lambda c: p.get(c, -1.0))
    if p.get(best, -1.0) < 0 and fallback:
        return fallback
    if current and current in candidates and best != current and seconds is not None:
        if seconds < min_secs or p.get(best, -1.0) - p.get(current, 0.0) < margin:
            return current
    return best


def default_follow_path() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return Path(base) / "tlwu_follow_lang"


def write_lang(path, lang: str) -> None:
    """Atomically record `lang`. Never raises (a state-file problem must not stop translation)."""
    try:
        p = Path(path)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(lang, encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def clear_lang(path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass


def read_lang(path, default: str = "en", allowed=SPEAK_LANGS) -> str:
    try:
        v = Path(path).read_text(encoding="utf-8").strip().lower()
    except OSError:
        return default
    return v if v in allowed else default
