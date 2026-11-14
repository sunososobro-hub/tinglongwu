"""Send modes for speak mode (#760): confirm (y+Enter), countdown (auto-send after COUNTDOWN_S unless stopped), auto.
Low-confidence lines never auto-send: they wait for an explicit "y" in every mode. Pure helpers, no I/O."""
from __future__ import annotations

import os
from difflib import SequenceMatcher

MODES = ("confirm", "countdown", "auto")
COUNTDOWN_S = 2.0  # seconds a line shows before countdown mode sends it (TLWU_SEND_COUNTDOWN overrides)
# Initial, uncalibrated guards (no eval data yet; see #760): override with the env vars below.
LOW_BACK_SCORE = 0.35  # similarity(zh, back-translation) below this = the meaning drifted
LOW_LOGPROB = -1.0  # Whisper avg_logprob below this = unsure (0.0 = unknown, never low); same limit as gate.py


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def back_score(zh: str, back: str) -> float:
    """0..1 character similarity between what was said and its back-translation."""
    a, b = "".join(zh.split()), "".join(back.split())
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def low_confidence(u, require_back: bool = True) -> str:
    """Reason string when `u` must wait for an explicit confirm, else "". `u` is a confirm.Utterance:
    u.extra["halluc"] (#759 flag) and u.extra["avg_logprob"] are optional; a back-translation that is still
    missing (require_back) counts as low, since the line cannot be checked."""
    extra = getattr(u, "extra", None) or {}
    if extra.get("halluc"):
        return "hallucination flag"
    lp = extra.get("avg_logprob") or 0.0
    if lp and lp < _env_float("TLWU_LOW_LOGPROB", LOW_LOGPROB):
        return f"avg logprob {lp:.2f}"
    if require_back:
        if not u.back:
            return "no back-translation yet"
        s = back_score(u.zh, u.back)
        if s < _env_float("TLWU_LOW_BACK_SCORE", LOW_BACK_SCORE):
            return f"back-translation mismatch {s:.2f}"
    return ""
