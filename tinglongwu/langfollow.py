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


def pick_candidate(probs, candidates, fallback: str | None = None) -> str:
    """Most probable language among `candidates`. probs: iterable of (lang, prob) or a dict."""
    items = probs.items() if hasattr(probs, "items") else probs
    p = {lang: prob for lang, prob in items}
    best = max(candidates, key=lambda c: p.get(c, -1.0))
    if p.get(best, -1.0) < 0 and fallback:
        return fallback
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
