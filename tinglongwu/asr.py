"""ASR interface + faster-whisper (CTranslate2; CPU int8 or GPU float16) implementation."""
from __future__ import annotations

import sys
import zlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .device import asr_compute_type, resolve_device
from .hallucination import is_hallucination
from .models import require
from .repeat import collapse_repeats, has_loop


@dataclass
class Transcript:
    text: str
    lang: str
    no_speech: float = 0.0  # Whisper no_speech_prob (max over kept pieces); feeds the hallucination filter


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


class FasterWhisperRecognizer(Recognizer):
    NO_SPEECH = 0.6

    def __init__(self, models_dir: Path, size: str = "small", lang: str = "auto",
                 device: str = "auto", glossary=None):
        path = require(models_dir / f"whisper-{size}", f"Whisper model '{size}'",
                       f"--whisper {size}")
        from faster_whisper import WhisperModel

        self.device = resolve_device(device)
        try:
            self.model = WhisperModel(str(path), device=self.device,
                                      compute_type=asr_compute_type(self.device))
        except RuntimeError as e:  # e.g. CUDA out of memory / driver problem at load time
            if self.device != "cuda":
                raise
            print(f"tlwu: GPU load failed ({e}); falling back to CPU.", file=sys.stderr)
            self.device = "cpu"
            self.model = WhisperModel(str(path), device="cpu", compute_type="int8")
        self.lang = None if lang == "auto" else lang  # auto: lock after first speech
        self._last = ""  # previous good output -> initial_prompt for the next segment
        self.glossary = glossary  # topic terms -> Whisper hotwords; number normalization

    def _post(self, text: str) -> str:
        text = collapse_repeats(text)  # safety net: one copy of any phrase still looped
        g = getattr(self, "glossary", None)
        return g.normalize_numbers(text) if g else text

    def _hot(self):
        g = getattr(self, "glossary", None)
        return g.hotwords() if g else None

    def partial(self, audio: np.ndarray) -> Transcript | None:
        """Streaming draft: greedy, no prompt, does not touch _last / the locked language."""
        segs, info = self.model.transcribe(
            audio, language=self.lang, task="transcribe", beam_size=1, vad_filter=False,
            condition_on_previous_text=False, no_speech_threshold=self.NO_SPEECH,
            log_prob_threshold=-1.0, compression_ratio_threshold=2.4, temperature=0.0,
            hotwords=self._hot())
        kept = [s for s in segs if s.text.strip()
                and not (s.no_speech_prob > self.NO_SPEECH and s.avg_logprob < -1.0)]
        text = self._post(" ".join(s.text.strip() for s in kept))
        return Transcript(text, self.lang or info.language, max(s.no_speech_prob for s in kept)) if text else None

    def _decode(self, audio: np.ndarray, retry: bool = False):
        # Normal pass: temperature fallback (0 -> 0.2 -> 0.4) so faster-whisper's own compression-ratio /
        # logprob checks can re-decode a stuck window (a single temperature has no fallback: loop leaks).
        # Retry pass after a detected loop: no previous-text prompt (that is what feeds loops), hotter
        # sampling, mild repetition penalty.
        kw = (dict(temperature=(0.3, 0.5, 0.7), repetition_penalty=1.1, initial_prompt=None) if retry
              else dict(temperature=(0.0, 0.2, 0.4), initial_prompt=make_prompt(self._last)))
        segs, info = self.model.transcribe(
            audio, language=self.lang, task="transcribe", beam_size=5, vad_filter=False,
            condition_on_previous_text=False, no_speech_threshold=self.NO_SPEECH,
            log_prob_threshold=-1.0, compression_ratio_threshold=2.4, hotwords=self._hot(), **kw)
        segs = [s for s in segs if not (s.no_speech_prob > self.NO_SPEECH and s.avg_logprob < -1.0)]
        return segs, info, " ".join(s.text.strip() for s in segs if s.text.strip())

    def transcribe(self, audio: np.ndarray) -> Transcript | None:
        segs, info, text = self._decode(audio)
        if text and has_loop(text):  # decoder got stuck: re-decode once without context, then collapse
            segs2, info2, text2 = self._decode(audio, retry=True)
            if text2 and not has_loop(text2):
                segs, info, text = segs2, info2, text2
        looped = bool(text) and has_loop(text)  # still looped after the retry: collapsed below, no prompt
        text = self._post(text)
        if not text:
            self._last = ""
            return None
        # context only when this output looks sane; a repeat/loop drops the prompt so it cannot propagate
        self._last = "" if looped or is_garbage(text, self._last) or is_hallucination(text) else text
        if self.lang is None:
            self.lang = info.language
        return Transcript(text, self.lang, max(s.no_speech_prob for s in segs))
