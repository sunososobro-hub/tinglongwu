"""Send modes for speak mode (#760): confirm (y+Enter), countdown (auto-send after COUNTDOWN_S unless stopped), auto.
Low-confidence lines never auto-send: they wait for an explicit "y" in every mode. Pure helpers (the notice only
reads the UI language), no I/O."""
from __future__ import annotations

import os
from difflib import SequenceMatcher

MODES = ("confirm", "countdown", "auto")
DIRECT_MODES = ("countdown", "auto")  # modes that can send a line without an explicit confirm
NOTICE_KEY = "gui_send_mode_notice"  # i18n key of the owner's one-line quality notice (#760, #834: same line as the GUI)
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


def notice(mode: str) -> str:
    """#822: the one-line quality notice in the UI language (zh-TW / en / ...) for a mode that can send without an
    explicit confirm (countdown, auto); "" for confirm and for anything else."""
    if mode not in DIRECT_MODES:
        return ""
    from ..i18n import t

    return t(NOTICE_KEY)


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


# SendDecider actions (#832)
SEND = "send"  # send the line now
DISCARD = "discard"  # drop the line
WAIT = "wait"  # countdown running: keep ticking
ASK = "ask"  # needs an explicit confirm (confirm mode, or a low-confidence line)


class SendDecider:
    """I/O-free send-mode state machine shared by the CLI and the GUI (#832). Time comes from the injected
    `clock`; nothing here reads input or sleeps. Events: line_ready(u), tick(), back_ready(u), confirm(),
    stop(), restart(). Each returns SEND / DISCARD / WAIT / ASK. A low-confidence line (low_confidence())
    never returns SEND without an explicit confirm(), in any mode."""

    def __init__(self, clock, mode: str = "confirm", countdown_s: float | None = None, require_back: bool = True):
        self.clock = clock
        self.mode = mode if mode in MODES else "confirm"
        self.countdown_s = countdown_s if countdown_s is not None else _env_float("TLWU_SEND_COUNTDOWN", COUNTDOWN_S)
        self.require_back = require_back
        self.state = "idle"  # idle | counting | asking | done (sent) | stopped (discarded)
        self.reason = ""  # why the line fell back to an explicit confirm ("" = none)
        self._u = None
        self._end = 0.0

    def line_ready(self, u) -> str:
        self._u = u
        self.reason = low_confidence(u, self.require_back)
        if self.mode == "auto" and not self.reason:
            self.state = "done"
            return SEND
        if self.mode == "countdown":
            self._end = self.clock() + self.countdown_s
            self.state = "counting"
            return WAIT
        self.state = "asking"
        return ASK

    def restart(self) -> str:
        """Restart the countdown (e.g. after a voice switch)."""
        if self.state == "counting":
            self._end = self.clock() + self.countdown_s
            return WAIT
        return ASK if self.state == "asking" else DISCARD

    def back_ready(self, u) -> str:
        """The back-translation arrived late: refresh the reason; the state does not change."""
        self._u = u
        self.reason = low_confidence(u, self.require_back)
        return WAIT if self.state == "counting" else (ASK if self.state == "asking" else DISCARD)

    def tick(self) -> str:
        if self.state != "counting":
            return ASK if self.state == "asking" else (SEND if self.state == "done" else DISCARD)
        if self.clock() < self._end:
            return WAIT
        self.reason = low_confidence(self._u, self.require_back)
        if self.reason:
            self.state = "asking"
            return ASK
        self.state = "done"
        return SEND

    def confirm(self) -> str:
        self.state = "done"
        return SEND

    def stop(self) -> str:
        self.state = "stopped"
        return DISCARD
