"""ASR interface + faster-whisper (CTranslate2; CPU int8 or GPU float16) implementation."""
from __future__ import annotations

import math
import os
import sys
import time
import zlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import threads as thread_budget
from .device import asr_compute_type, cpu_threads, resolve_device
from .hallucination import is_hallucination
from .langfollow import SPEAK_LANGS, clear_lang, pick_candidate, script_lang, write_lang
from .models import require
from .repeat import collapse_repeats, filter_hallucination, has_loop


@dataclass
class Transcript:
    text: str
    lang: str
    no_speech: float = 0.0  # Whisper no_speech_prob (max over kept pieces); feeds the hallucination filter
    avg_logprob: float = 0.0  # Whisper avg_logprob (mean over kept pieces; 0.0 = unknown); speak-side gate (#249)


def _logprob(segs) -> float:
    lp = [s.avg_logprob for s in segs if getattr(s, "avg_logprob", None) is not None]
    return sum(lp) / len(lp) if lp else 0.0


PROMPT_MAX_CHARS = 100  # previous text handed to Whisper as initial_prompt


def is_garbage(text: str, prev: str = "") -> bool:
    """Output that must not be fed back as context: empty, a repeat of the last output, or a loop."""
    t = " ".join(text.split())
    if not t:
        return True
    if prev and t == " ".join(prev.split()):
        return True
    raw = t.encode("utf-8")
    return len(raw) >= 40 and len(raw) / len(zlib.compress(raw)) > 2.4  # repetition loop


# #251 worst-case limits for live decoding (speak side, CPU listen side). File runs keep the full fallback.
SAMPLE_RATE = 16000
FILE_TEMPS = (0.0, 0.2, 0.4)        # normal pass: temperature fallback
RETRY_TEMPS = (0.3, 0.5, 0.7)       # after a detected loop
LIVE_TEMP_PASSES = 1                # TLWU_ASR_TEMP_PASSES: live passes per decode (1..3)
TOKENS_PER_S = 12                   # TLWU_ASR_TOKENS_PER_S: max_new_tokens per second of audio (live)
MIN_NEW_TOKENS = 24                 # floor for very short segments
MAX_NEW_TOKENS = 220                # Whisper context is 448 tokens; leaves room for the <=100-char prompt
BUDGET_FACTOR = 3.0                 # TLWU_ASR_BUDGET_FACTOR: per-sentence budget = factor x audio length
BUDGET_MIN_S = 4.0                  # TLWU_ASR_BUDGET_MIN: ... but never below this


def _env(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return v if v > 0 else default


def live_temps(base: tuple) -> tuple:
    """The first N temperatures of `base` (N = TLWU_ASR_TEMP_PASSES, default 1, clamped to 1..len)."""
    n = int(_env("TLWU_ASR_TEMP_PASSES", LIVE_TEMP_PASSES))
    return tuple(base[:max(1, min(n, len(base)))])


def token_cap(seconds: float) -> int:
    """max_new_tokens for a live segment: TOKENS_PER_S per second, within [MIN_NEW_TOKENS, MAX_NEW_TOKENS]."""
    return min(MAX_NEW_TOKENS, max(MIN_NEW_TOKENS, math.ceil(seconds * _env("TLWU_ASR_TOKENS_PER_S", TOKENS_PER_S))))


def time_budget(seconds: float) -> float:
    """Seconds one live sentence may spend in ASR before it is dropped."""
    return max(_env("TLWU_ASR_BUDGET_MIN", BUDGET_MIN_S), _env("TLWU_ASR_BUDGET_FACTOR", BUDGET_FACTOR) * seconds)


class OverBudget(Exception):
    """A live decode ran past its time budget (#251)."""


def make_prompt(prev: str, max_chars: int = PROMPT_MAX_CHARS) -> str | None:
    t = " ".join(prev.split())
    return t[-max_chars:] if t else None


class Recognizer(ABC):
    @abstractmethod
    def transcribe(self, audio: np.ndarray) -> Transcript | None:
        """float32 mono 16 kHz -> Transcript, or None if no speech."""

    def partial(self, audio: np.ndarray) -> Transcript | None:
        """Cheap draft pass for --stream (no state changes). Default: the normal path."""
        return self.transcribe(audio)


def cpu_fallback_settings(beam, threads, cores=None, ram_mib=None) -> tuple[int, int | None]:
    """#780: (beam, cpu_threads) for a CPU model loaded after a CUDA load failure. Values already given (the CPU
    policy ran) win; otherwise the hwprobe.cpu_mode_settings policy; TLWU_CPU_THREADS overrides the thread count."""
    from .hwprobe import cpu_mode_settings, ram_mib as _ram

    if not beam or not threads:
        mib = ram_mib if ram_mib is not None else _ram()
        st = cpu_mode_settings(cores or os.cpu_count() or 4, mib / 1024 if mib else 16.0)
        beam, threads = beam or st.beam, threads or st.threads
    env = (os.environ.get("TLWU_CPU_THREADS") or "").strip()
    if env.isdigit() and int(env) > 0:
        threads = int(env)
    return beam, threads


class FasterWhisperRecognizer(Recognizer):
    NO_SPEECH = 0.6

    def __init__(self, models_dir: Path, size: str = "small", lang: str = "auto",
                 device: str = "auto", glossary=None, candidates=None, lang_file=None,
                 fast: bool = False, live: bool = False, beam: int | None = None,
                 threads: int | None = None):
        threads_arg = threads
        path = require(models_dir / f"whisper-{size}", f"Whisper model '{size}'",
                       f"--whisper {size}")
        from faster_whisper import WhisperModel

        self.device = resolve_device(device)
        # #186 fast: live CPU speak mode. Greedy decode, no timestamp tokens, all physical cores. CUDA never uses it.
        self.fast = bool(fast) and self.device == "cpu"
        self.beam = 1 if self.fast else 5
        cpu_mode = self.device == "cpu" and not self.fast  # #780: CPU-mode auto settings (explicit args only)
        if cpu_mode and beam:
            self.beam = beam
        threads = {}
        if cpu_mode and threads_arg and not thread_budget.both():
            threads = {"cpu_threads": threads_arg}
        elif self.device == "cpu" and thread_budget.both():  # #250 listen+speak: this process's share of the budget
            threads = {"cpu_threads": thread_budget.current()["asr"]}
        elif self.fast:
            threads = {"cpu_threads": cpu_threads()}
        try:
            self.model = WhisperModel(str(path), device=self.device,
                                      compute_type=asr_compute_type(self.device), **threads)
        except RuntimeError as e:  # e.g. CUDA out of memory / driver problem at load time
            if self.device != "cuda":
                raise
            print(f"tlwu: GPU load failed ({e}); falling back to CPU.", file=sys.stderr)
            self.device = "cpu"
            beam_cpu, threads_cpu = cpu_fallback_settings(beam, threads_arg)  # #780: CPU-mode settings after a fallback
            if thread_budget.both():  # listen+speak: this process's share of the shared budget, as on the normal CPU path
                kw = {"cpu_threads": thread_budget.current()["asr"]}
            else:
                kw = {"cpu_threads": threads_cpu} if threads_cpu else {}
            self.model = WhisperModel(str(path), device="cpu", compute_type="int8", **kw)
            self.fast, self.beam = False, beam_cpu
        self.lang = None if lang == "auto" else lang  # auto: lock after first speech
        self.auto = lang == "auto"
        # auto + candidates: re-detect every segment, restricted to the candidate set (never locks)
        self.candidates = tuple(candidates) if (candidates and lang == "auto") else None
        self.lang_file = lang_file if self.candidates else None  # last detected en/ru for `reverse.proto --to follow`
        self._cur = None  # language of the latest segment (candidate mode)
        if self.lang_file:
            clear_lang(self.lang_file)  # no stale language from an earlier session
        self._last = ""  # previous good output -> initial_prompt for the next segment
        self.live = bool(live)  # #251: capped retries, token cap and a per-sentence time budget
        self.glossary = glossary  # topic terms -> Whisper hotwords; number normalization

    def _lang_for(self, audio: np.ndarray, reuse: bool = False) -> str | None:
        """Language to decode with: the fixed/locked one, or (candidate mode) the most probable candidate."""
        cands = getattr(self, "candidates", None)
        if not cands:
            return self.lang
        if reuse and getattr(self, "_cur", None):
            return self._cur
        _lang, _p, probs = self.model.detect_language(audio)
        return pick_candidate(probs, cands, fallback=_lang if _lang in cands else cands[0],
                              current=getattr(self, "_cur", None), seconds=len(audio) / 16000.0)

    def _post(self, text: str) -> str:
        text = filter_hallucination(text)  # one copy of any looped phrase; drop what is still a loop
        g = getattr(self, "glossary", None)
        return g.normalize_numbers(text) if g else text

    def _hot(self):
        g = getattr(self, "glossary", None)
        return g.hotwords() if g else None

    def partial(self, audio: np.ndarray) -> Transcript | None:
        """Streaming draft: greedy, no prompt, does not touch _last / the locked language."""
        lang = self._lang_for(audio, reuse=True)
        segs, info = self.model.transcribe(
            audio, language=lang, task="transcribe", beam_size=1, vad_filter=False,
            condition_on_previous_text=False, no_speech_threshold=self.NO_SPEECH,
            log_prob_threshold=-1.0, compression_ratio_threshold=2.4, temperature=0.0,
            hotwords=self._hot())
        kept = [s for s in segs if s.text.strip()
                and not (s.no_speech_prob > self.NO_SPEECH and s.avg_logprob < -1.0)]
        text = self._post(" ".join(s.text.strip() for s in kept))
        return Transcript(text, script_lang(text, lang or info.language),
                          max(s.no_speech_prob for s in kept), _logprob(kept)) if text else None

    def _decode(self, audio: np.ndarray, retry: bool = False, lang: str | None = None, deadline=None):
        lang = self.lang if lang is None else lang
        # Normal pass: temperature fallback (0 -> 0.2 -> 0.4) so faster-whisper's own compression-ratio /
        # logprob checks can re-decode a stuck window (a single temperature has no fallback: loop leaks).
        # Retry pass after a detected loop: no previous-text prompt (that is what feeds loops), hotter
        # sampling, mild repetition penalty.
        # Live (#251): only the first temperature(s), max_new_tokens scaled to the audio length.
        live = getattr(self, "live", False)
        temps = RETRY_TEMPS if retry else FILE_TEMPS
        kw = (dict(temperature=temps, repetition_penalty=1.1, initial_prompt=None) if retry
              else dict(temperature=temps, initial_prompt=make_prompt(self._last)))
        if live:
            kw.update(temperature=live_temps(temps), max_new_tokens=token_cap(len(audio) / SAMPLE_RATE))
        gen, info = self.model.transcribe(
            audio, language=lang, task="transcribe", beam_size=getattr(self, "beam", 5), vad_filter=False,
            condition_on_previous_text=False, no_speech_threshold=self.NO_SPEECH,
            log_prob_threshold=-1.0, compression_ratio_threshold=2.4, hotwords=self._hot(),
            without_timestamps=bool(getattr(self, "fast", False)), **kw)
        segs = []
        for s in gen:  # faster-whisper decodes lazily: stop pulling once the sentence is over budget
            if deadline is not None and self._now() > deadline:
                raise OverBudget
            if not (s.no_speech_prob > self.NO_SPEECH and s.avg_logprob < -1.0):
                segs.append(s)
        if deadline is not None and self._now() > deadline:
            raise OverBudget
        return segs, info, " ".join(s.text.strip() for s in segs if s.text.strip())

    def _now(self) -> float:
        return getattr(self, "clock", time.monotonic)()

    def _drop(self, budget: float, started: float) -> None:
        self.dropped = getattr(self, "dropped", 0) + 1
        self._last = ""
        print(f"tlwu: asr: dropped a sentence over its time budget ({self._now() - started:.1f} s > "
              f"{budget:.1f} s; {self.dropped} dropped so far)", file=sys.stderr, flush=True)

    def transcribe(self, audio: np.ndarray) -> Transcript | None:
        if not getattr(self, "live", False):
            return self._transcribe(audio, None)
        started = self._now()
        budget = time_budget(len(audio) / SAMPLE_RATE)
        try:
            return self._transcribe(audio, started + budget)
        except OverBudget:  # drop this sentence, the caller moves on to the next segment
            self._drop(budget, started)
            return None

    def _transcribe(self, audio: np.ndarray, deadline) -> Transcript | None:
        lang = self._lang_for(audio)
        segs, info, text = self._decode(audio, lang=lang, deadline=deadline)
        if text and has_loop(text):  # decoder got stuck: re-decode once without context, then collapse
            segs2, info2, text2 = self._decode(audio, retry=True, lang=lang, deadline=deadline)
            if text2 and not has_loop(text2):
                segs, info, text = segs2, info2, text2
        looped = bool(text) and has_loop(text)  # still looped after the retry: collapsed below, no prompt
        text = self._post(text)
        if not text:
            self._last = ""
            return None
        # context only when this output looks sane; a repeat/loop drops the prompt so it cannot propagate
        self._last = "" if looped or is_garbage(text, self._last) or is_hallucination(text) else text
        if getattr(self, "candidates", None):
            lang = script_lang(text, lang)  # kana in a "zh" segment: it is Japanese (#86)
            self._cur = lang
            if lang in SPEAK_LANGS and getattr(self, "lang_file", None):
                write_lang(self.lang_file, lang)  # zh segments leave the speak target alone
            return Transcript(text, lang, max(s.no_speech_prob for s in segs), _logprob(segs))
        if self.lang is None:
            self.lang = script_lang(text, info.language)
        elif getattr(self, "auto", False) and script_lang(text, self.lang) != self.lang:
            self.lang = "ja"  # auto locked onto zh by a kanji-only first line; kana shows it is Japanese (#86)
        return Transcript(text, script_lang(text, self.lang), max(s.no_speech_prob for s in segs), _logprob(segs))
