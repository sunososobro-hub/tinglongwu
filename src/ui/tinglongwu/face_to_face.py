"""#1164: face-to-face mode on ONE capture source: each finished utterance is translated mine -> partner or
partner -> mine, chosen from the ASR-detected language (`Transcript.lang`). Pure routing; no audio, no models."""
from __future__ import annotations

from .langs import SameLanguageError, lang_base


def candidates(mine: str, partner: str) -> tuple[str, ...]:
    """Whisper language candidates for the session (restricts detection to the two languages, see asr `candidates`).
    zh-tw / zh-cn collapse to "zh". Raises SameLanguageError when both sides are the same language."""
    a, b = lang_base(mine), lang_base(partner)
    if not a or not b or a == b:
        raise SameLanguageError(f"My language and partner language are both {a or b!r}")
    return (a, b)


def route(mine: str, partner: str, detected: str | None) -> dict:
    """Direction for one utterance. Returns {"src", "target"}: the language it was spoken in and what to translate to.
    detected == mine -> translate to the partner language; detected == partner -> translate to mine;
    any other language (or unknown) falls back to translating into mine."""
    candidates(mine, partner)  # validates: raises SameLanguageError
    d = lang_base(detected)
    if d == lang_base(mine):
        return {"src": mine, "target": partner}
    return {"src": d or partner, "target": mine}
