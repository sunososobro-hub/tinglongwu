"""#1369: on a CPU that cannot keep up, live subtitles stay close to the present (bounded lag, stated catch-up policy)
instead of drifting further behind (owner field test: ~33 s behind after 40 s of video, growing)."""
import re
import sys
import threading
import time

import numpy as np
import pytest

from tinglongwu import pipeline
from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.hallucination import is_hallucination
from tinglongwu.i18n import t as _msg
from tinglongwu.pipeline import run
from tinglongwu.sink import Sink
from tinglongwu.translate import Translation, Translator

K = 0.05  # time scale: 1 simulated second = 50 ms of wall time (a 180 s clip runs in ~9 s)
SR = 16000
SEG_S = 3.0  # the clip arrives as one 3 s speech segment every 3 s (real time)
CLIP_S = 180.0
ASR_FIX, ASR_PER_S = 2.5, 0.25  # per call: fixed (Whisper pads to a 30 s window) + per audio second -> RTF 1.08 on 3 s
MT_S, BUSY = 1.0, 1.5  # MT per line; ASR runs 1.5x slower while MT holds the CPU (one extra busy core on 4 vCPU)


def _text(idx):
    return " ".join(f"n{i}" for i in idx) + "."  # distinct words: the #1255 loop filter keeps a joined line whole


def _idx(text):
    return [int(m) for m in re.findall(r"\bn(\d+)\b", text)]


def _val(i):
    return 0.1 + i / 10000.0  # each segment's samples carry its index, so a joined segment can be decoded


class Clip:
    """Live source: segment i (SEG_S of audio) is complete at t0 + (i + 1) * SEG_S."""

    def __init__(self, n):
        self.n, self.t_end = n, {}

    def frames(self):
        t0 = time.monotonic()
        for i in range(self.n):
            due = t0 + (i + 1) * SEG_S * K
            time.sleep(max(0.0, due - time.monotonic()))
            self.t_end[i] = time.monotonic()
            yield np.full(int(SEG_S * K * SR), _val(i), dtype="float32")

    def close(self):
        pass


class OneSeg:
    def segments(self, frames):
        yield from frames


class Cpu:
    def __init__(self):
        self.mt_busy = 0


class SlowASR(Recognizer):
    def __init__(self, cpu):
        self.cpu, self.calls = cpu, 0

    def transcribe(self, audio):
        busy = BUSY if self.cpu.mt_busy else 1.0
        time.sleep((ASR_FIX + ASR_PER_S * len(audio) / SR / K) * busy * K)
        self.calls += 1
        return Transcript(_text(_idx_of(audio)), "en")


class SlowMT(Translator):
    def __init__(self, cpu):
        self.cpu = cpu

    def translate(self, text, src, tgt):
        self.cpu.mt_busy += 1
        try:
            time.sleep(MT_S * K)
        finally:
            self.cpu.mt_busy -= 1
        return Translation(None, "譯:" + text)


class Shown(Sink):
    def __init__(self):
        self.lines = []  # (wall time, Result)
        self.lock = threading.Lock()

    def write(self, r):
        with self.lock:
            self.lines.append((time.monotonic(), r))


def _scaled(monkeypatch, old=False):
    for name in ("MT_YIELD_S", "MT_HOLD_POLL", "ASR_JOIN_S", "ASR_JOIN_MAX_S", "MT_STALE_S", "MT_HOLD_MAX"):
        monkeypatch.setattr(pipeline, name, getattr(pipeline, name) * K)
    if old:  # the policy before #1369: 30 s hold, no catch-up
        monkeypatch.setattr(pipeline, "MT_HOLD_MAX", 30.0 * K)
        for name in ("ASR_JOIN_S", "MT_STALE_S"):
            monkeypatch.setattr(pipeline, name, float("inf"))


def _simulate(monkeypatch, old=False):
    _scaled(monkeypatch, old)
    cpu = Cpu()
    asr = SlowASR(cpu)
    src, sink, notes = Clip(int(CLIP_S / SEG_S)), Shown(), []
    stop = threading.Event()
    th = threading.Thread(target=run, args=(src, OneSeg(), asr, SlowMT(cpu), sink, "zh"),
                          kwargs={"stop_event": stop, "on_catchup": notes.append}, daemon=True)
    t0 = time.monotonic()
    th.start()
    time.sleep(CLIP_S * K + 0.1)
    stop.set()
    th.join(5)

    def lag(at_s):
        """Subtitle lag at `at_s` simulated seconds into the clip: for the newest translated line shown by then,
        the simulated seconds from the end of its newest segment to the moment it was shown."""
        at = t0 + at_s * K
        shown = [(when, max(_idx(r.original))) for when, r in sink.lines if when <= at and r.target.startswith("譯:")]
        assert shown, f"no translated line by {at_s} s"
        when, newest = shown[-1]
        return (when - src.t_end[newest]) / K

    return lag(60), lag(CLIP_S), sink, notes, [asr.calls]


def test_lag_stays_bounded_on_a_cpu_that_cannot_keep_up(monkeypatch):
    lag60, lag180, sink, notes, calls = _simulate(monkeypatch)
    assert lag60 <= 8.0, (lag60, lag180)
    assert lag180 - lag60 <= 2.0, (lag60, lag180)
    assert calls[0] < CLIP_S / SEG_S - 5, "waiting segments were transcribed together"


def test_old_policy_drifts(monkeypatch):
    """The same machine with the pre-#1369 policy: translations fall far behind and the gap grows (the field test)."""
    lag60, lag180, _, _, _ = _simulate(monkeypatch, old=True)
    assert lag60 > 8.0 or lag180 - lag60 > 2.0, (lag60, lag180)


class Burst:
    """Live source: `n` one-second segments arrive at once."""

    def __init__(self, n):
        self.n = n

    def frames(self):
        for i in range(self.n):
            yield np.full(SR, _val(i), dtype="float32")

    def close(self):
        pass


class StallASR(SlowASR):
    """The first call takes `stall` s (a CPU spike), the rest are instant."""

    def __init__(self, stall):
        super().__init__(Cpu())
        self.stall, self.sizes = stall, []

    def transcribe(self, audio):
        if not self.sizes:
            time.sleep(self.stall)
        self.sizes.append(len(audio) / SR)
        return Transcript(_text(_idx_of(audio)), "en")


def _idx_of(audio):
    return sorted({int(round((v - 0.1) * 10000)) for v in np.unique(audio)})


def _heard(sink):
    return [i for _, r in sink.lines for i in _idx(r.original)]


def test_backlog_is_joined_without_losing_audio(monkeypatch):
    """ASR stalled past ASR_JOIN_S: the waiting segments are transcribed together; every word is kept, in order."""
    monkeypatch.setattr(pipeline, "ASR_JOIN_S", 0.05)
    monkeypatch.setattr(pipeline, "ASR_JOIN_MAX_S", 8.0)
    asr, sink = StallASR(0.2), Shown()
    run(Burst(20), OneSeg(), asr, None, sink, "zh")
    assert asr.sizes[0] == 1.0 and max(asr.sizes) == 8.0 and len(asr.sizes) < 6, asr.sizes
    assert _heard(sink) == list(range(20))
    assert sum(asr.sizes) == 20.0  # no audio dropped (#1263)


def test_no_join_while_asr_keeps_up(monkeypatch):
    """A burst that ASR clears within ASR_JOIN_S is transcribed one segment per call, as before."""
    asr, sink = StallASR(0.0), Shown()
    run(Burst(12), OneSeg(), asr, None, sink, "zh")
    assert asr.sizes == [1.0] * 12 and _heard(sink) == list(range(12))


def test_offline_file_is_never_joined(monkeypatch):
    monkeypatch.setattr(pipeline, "ASR_JOIN_S", 0.05)
    asr, sink = StallASR(0.2), Shown()
    run(Burst(6), OneSeg(), asr, None, sink, "zh", audio_clock=True)
    assert asr.sizes == [1.0] * 6


@pytest.mark.parametrize("text", ["Music.", "Music. Music.", "[Music]", "(upbeat music)", "[MUSIC PLAYING]", "♪♪",
                                  "音樂。音樂。", "Музыка"])
def test_music_only_segment_produces_no_line(text):
    assert is_hallucination(text, 0.1, 0.0)


@pytest.mark.parametrize("text", ["I love music.", "♪ I love you ♪", "[Music] Welcome back everyone", "[Applause]"])
def test_speech_with_music_words_is_kept(text):
    assert not is_hallucination(text, 0.1, 0.0)


def test_stale_lines_keep_source_and_say_skipped(monkeypatch):
    """MT slower than the speech: old lines are written with their source and "[skipped to catch up]"."""
    monkeypatch.setattr(sys.modules[__name__], "MT_S", 6.0)  # one line every ~3 s, MT needs 6 s per line
    monkeypatch.setattr(sys.modules[__name__], "BUSY", 1.0)
    lag60, lag180, sink, notes, _ = _simulate(monkeypatch)
    skipped = [r for _, r in sink.lines if r.target == _msg("mt_skipped")]
    assert skipped and all(_idx(r.original) for r in skipped)  # the source text is kept (saved transcript)
    assert notes[-1] == len(skipped)  # reported for the status line
    assert lag180 - lag60 <= 2.0, (lag60, lag180)
