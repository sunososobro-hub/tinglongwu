"""Speak-side input gate (#249): noise, too-short segments and repeat hallucinations never reach the confirm queue.

gate(stats) is a pure function: (ok, reason). Every threshold is a named constant below and can be overridden with
an environment variable (TLWU_GATE_*), read once per gate() call so tests and power users can tune it.

Where the numbers come from:
- MIN_DUR_S 0.25 s: Silero VAD's own default min_speech_duration_ms=250 (a shorter blip is a click/cough).
- MIN_RMS 1e-3: the silence floor speak mode already used (live.recognize_utterances silence_rms default).
- NO_SPEECH_MAX 0.6 / LOGPROB_MIN -1.0: Whisper's own no_speech_threshold / logprob_threshold (openai/whisper
  transcribe() defaults, also what asr.FasterWhisperRecognizer passes). Whisper only drops a piece when BOTH hold;
  on the speak side a wrong sentence goes into someone else's meeting, so either one alone rejects it.
- COMPRESSION_MAX 2.4: Whisper's compression_ratio_threshold (same as asr.is_garbage): above it the text is a loop.
- MIN_CHARS 2: one letter/character ("嗯", "a") is a filler or a noise transcription, not a sentence to send.
- FILLER (#312): text made only of filler syllables (哈/蛤/嗯/啊/欸 ..., "uh"/"um" ...) or one character/word said
  FILLER_REPEAT (4) or more times ("他他他他他他 他"). 4 is repeat.py's own word-loop limit ("да да да" is speech,
  4+ identical words is a loop); real reduplication stops at 3 (對對對, 走走走) and 謝謝 / 好好 stay sentences.
"""
from __future__ import annotations

import os
import re
import sys
import time
import zlib
from dataclasses import dataclass

MIN_DUR_S = 0.25
MIN_RMS = 1e-3
NO_SPEECH_MAX = 0.6
LOGPROB_MIN = -1.0
COMPRESSION_MAX = 2.4
MIN_CHARS = 2
FILLER_REPEAT = 4
# #312: interjections / hesitation sounds that carry nothing to translate (simplified + traditional forms)
FILLER_CHARS = frozenset("哈蛤嗯啊欸呃喔哦噢唉诶誒嘿呵恩额額哇呀")
FILLER_WORDS = frozenset({"uh", "uhh", "um", "umm", "uhm", "hmm", "hm", "mm", "mhm", "ah", "oh", "er", "erm", "eh",
                          "ha", "haha", "hahaha"})
_UNIT = re.compile(r"[぀-ヿ㐀-鿿가-힯]|[^\W\d_぀-ヿ㐀-鿿가-힯]+|\d+")
REPORT_EVERY_S = 60.0  # at most one summary line of gate drops per minute (no console flood)

ENV = {"min_dur": ("TLWU_GATE_MIN_DUR", MIN_DUR_S), "min_rms": ("TLWU_GATE_MIN_RMS", MIN_RMS),
       "no_speech": ("TLWU_GATE_NO_SPEECH", NO_SPEECH_MAX), "logprob": ("TLWU_GATE_LOGPROB", LOGPROB_MIN),
       "compression": ("TLWU_GATE_COMPRESSION", COMPRESSION_MAX), "min_chars": ("TLWU_GATE_MIN_CHARS", MIN_CHARS)}


def _env_float(name: str, default: float, env=None) -> float:
    env = os.environ if env is None else env
    try:
        return float(env[name])
    except (KeyError, ValueError):
        return default


def limits(env=None) -> dict:
    return {k: _env_float(name, d, env) for k, (name, d) in ENV.items()}


@dataclass
class SegmentStats:
    duration: float  # seconds of audio
    rms: float
    text: str = ""
    no_speech: float = 0.0  # Whisper no_speech_prob (max over pieces)
    avg_logprob: float = 0.0  # Whisper avg_logprob (mean over pieces; 0.0 = unknown)


def compression_ratio(text: str) -> float:
    raw = " ".join(text.split()).encode("utf-8")
    return len(raw) / len(zlib.compress(raw)) if raw else 0.0


def _chars(text: str) -> int:
    return len(re.findall(r"[^\W_]", text))  # letters, digits and CJK; no spaces or punctuation


def is_filler(text: str) -> bool:
    """#312: True for an utterance with nothing to translate: only filler syllables ("蛤蛤蛤蛤蛤", "嗯啊", "uh um"),
    or a single character / word repeated FILLER_REPEAT+ times ("他他他他他他 他"). CJK counts per character."""
    units = [u.lower() for u in _UNIT.findall(text)]
    if not units:
        return False
    if all(u in FILLER_CHARS or u in FILLER_WORDS for u in units):
        return True
    return len(units) >= FILLER_REPEAT and len(set(units)) == 1


def gate_audio(duration: float, rms: float, env=None, **over) -> tuple[bool, str]:
    """The audio-only part of gate(), checked before ASR runs (no Whisper time spent on a click or on silence)."""
    lim = {**limits(env), **over}
    if duration < lim["min_dur"]:
        return False, "too_short"
    if rms < lim["min_rms"]:
        return False, "quiet"
    return True, ""


def gate(s: SegmentStats, env=None, **over) -> tuple[bool, str]:
    """(True, "") when the segment may go to the confirm queue, else (False, reason).
    reason in: too_short, quiet, no_speech, low_logprob, few_chars, compression, filler, repeat.
    `over` replaces single limits (keys of ENV), e.g. min_rms=..."""
    from ..hallucination import is_hallucination
    from ..repeat import has_loop

    ok, why = gate_audio(s.duration, s.rms, env, **over)
    if not ok:
        return ok, why
    lim = {**limits(env), **over}
    if s.no_speech > lim["no_speech"]:
        return False, "no_speech"
    if s.avg_logprob < lim["logprob"]:
        return False, "low_logprob"
    if _chars(s.text) < lim["min_chars"]:
        return False, "few_chars"
    if compression_ratio(s.text) > lim["compression"]:
        return False, "compression"
    if is_filler(s.text):
        return False, "filler"
    if has_loop(s.text) or is_hallucination(s.text, s.rms, s.no_speech):
        return False, "repeat"
    return True, ""


class DropReport:
    """Counts gate drops by reason; report() prints one summary line at most every REPORT_EVERY_S seconds."""

    def __init__(self, out=None, clock=time.monotonic, every: float = REPORT_EVERY_S):
        self.out = out or (lambda m: print(m, file=sys.stderr, flush=True))
        self.clock, self.every = clock, every
        self.counts: dict[str, int] = {}
        self._last = float("-inf")  # the first drop is reported at once, then at most once per `every`

    def add(self, reason: str) -> None:
        self.counts[reason] = self.counts.get(reason, 0) + 1
        self.report()

    def report(self, force: bool = False) -> None:
        if not self.counts or (not force and self.clock() - self._last < self.every):
            return
        n = sum(self.counts.values())
        detail = ", ".join(f"{k}={v}" for k, v in sorted(self.counts.items()))
        self.out(f"speak gate: dropped {n} segment(s) ({detail})")
        self.counts.clear()
        self._last = self.clock()
