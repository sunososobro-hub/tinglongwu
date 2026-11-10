"""#35: ASR must not wait for MT (lines arrived in bursts on CPU); --debug-latency logs per-stage times."""
import threading

import numpy as np
import pytest

from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.translate import PivotTranslator, Translation, Translator


class Tone:
    def __init__(self, n):
        self.n = n

    def frames(self):
        for _ in range(self.n):
            yield np.full(16000, 0.1, np.float32)

    def close(self):
        pass


class CountingASR(Recognizer):
    def __init__(self):
        self.n = 0
        self.second = threading.Event()

    def transcribe(self, audio):
        self.n += 1
        if self.n >= 2:
            self.second.set()
        return Transcript(f"line {self.n}", "ru")


class SlowMT(Translator):
    """Blocks the first line until ASR has handled the next segment (or 2 s pass, as serial code would)."""

    def __init__(self, asr):
        self.asr, self.overlapped = asr, None

    def translate(self, text, src, tgt):
        if self.overlapped is None:
            self.overlapped = self.asr.second.wait(2.0)
        return Translation(None, "zh " + text)


class ListSink:
    def __init__(self):
        self.rows = []

    def write(self, r):
        self.rows.append((r.original, r.target))


def test_asr_runs_while_mt_is_busy():
    asr = CountingASR()
    mt, sink = SlowMT(asr), ListSink()
    run(Tone(4), Segmenter(chunk=1.0, use_vad=False), asr, mt, sink, "zh")
    assert mt.overlapped is True  # serial ASR -> MT would time out here
    assert sink.rows[:3] == [("line 1", "zh line 1"), ("line 2", "zh line 2"), ("line 3", "zh line 3")]


def test_mt_error_reaches_caller():
    class Broken(Translator):
        def translate(self, text, src, tgt):
            raise RuntimeError("mt boom")

    with pytest.raises(RuntimeError, match="mt boom"):
        run(Tone(3), Segmenter(chunk=1.0, use_vad=False), CountingASR(), Broken(), ListSink(), "zh")


def test_debug_latency_per_stage(tmp_path):
    steps = {("ru", "en"): lambda x: "hello", ("en", "zh"): lambda x: "你好"}
    logs = []
    run(Tone(2), Segmenter(chunk=1.0, use_vad=False), CountingASR(),
        PivotTranslator(tmp_path, "tw", lambda s, t: steps[(s, t)]), ListSink(), "zh", log=logs.append)
    assert any(m.startswith("latency: asr ") and "s of audio" in m for m in logs)
    mt = [m for m in logs if m.startswith("latency: mt ")]
    assert mt and "ru-en " in mt[0] and "en-zh " in mt[0] and "in queue" in mt[0]
