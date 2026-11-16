"""#1253: MT yields the CPU to ASR while ASR is behind, so turning MT on drops no more sentences than ASR-only."""
import io
import threading
import time

import numpy as np

from tinglongwu import pipeline
from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.pipeline import run
from tinglongwu.sink import TextSink
from tinglongwu.translate import Translation, Translator

WORDS = ["один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь"]


class Burst:
    """Live source (no audio clock): `n` one-second segments arrive at once, as after a slow stretch."""

    def __init__(self, n):
        self.n = n

    def frames(self):
        for _ in range(self.n):
            yield np.full(16000, 0.3, dtype="float32")

    def close(self):
        pass


class OneSeg:
    def segments(self, frames):
        yield from frames


class Cpu:
    """Shared CPU: a sentence whose ASR overlaps an MT call misses its time budget and is dropped (the
    field-test shape: MT threads slow Whisper down past the #251 per-sentence budget)."""

    def __init__(self):
        self.mt_busy = 0
        self.lock = threading.Lock()


class BudgetASR(Recognizer):
    def __init__(self, cpu, secs=0.08):
        self.cpu, self.secs, self.n, self.dropped = cpu, secs, 0, 0

    def transcribe(self, audio):
        contended = self.cpu.mt_busy > 0
        time.sleep(self.secs)
        contended = contended or self.cpu.mt_busy > 0
        self.n += 1
        if contended:
            self.dropped += 1
            return None
        return Transcript(f"привет номер {WORDS[(self.n - 1) % len(WORDS)]}", "ru")


class BusyMT(Translator):
    def __init__(self, cpu, secs=0.15):
        self.cpu, self.secs, self.calls = cpu, secs, 0

    def translate(self, text, src, tgt):
        with self.cpu.lock:
            self.cpu.mt_busy += 1
        try:
            time.sleep(self.secs)
        finally:
            with self.cpu.lock:
                self.cpu.mt_busy -= 1
        self.calls += 1
        return Translation(None, "你好")


def _drops(with_mt: bool, n=6, **kw):
    cpu = Cpu()
    asr = BudgetASR(cpu)
    mt = BusyMT(cpu) if with_mt else None
    out = io.StringIO()
    run(Burst(n), OneSeg(), asr, mt, TextSink(out), **kw)
    return asr, mt, out.getvalue()


def test_mt_on_drops_no_more_than_asr_only():
    """Acceptance shape: same input, ASR-only vs ASR+MT -> same dropped-sentence count, every line translated."""
    asr_only, _, _ = _drops(False)
    asr, mt, out = _drops(True)
    assert asr_only.dropped == 0
    assert asr.dropped == asr_only.dropped
    assert mt.calls == 6 and out.count("你好") == 6  # translations are late, not lost


def test_mt_delay_is_reported():
    """The held translations are shown (on_mt_wait > 0, then 0) and logged per line (on_record mt_hold)."""
    waits, recs = [], []
    asr, mt, _ = _drops(True, on_mt_wait=waits.append, on_record=recs.append)
    assert max(waits) >= 1 and waits[-1] == 0
    assert len(recs) == 6 and max(r["mt_hold"] for r in recs) > 0
    assert all(r["mt"] >= r["mt_hold"] for r in recs)


def test_hold_is_capped(monkeypatch):
    """A CPU that never catches up still gets translations: each line waits at most MT_HOLD_MAX from when it was
    queued (time behind an earlier held line counts), so MT starts while ASR is still busy."""
    monkeypatch.setattr(pipeline, "MT_HOLD_MAX", 0.2)
    cpu = Cpu()
    asr = BudgetASR(cpu, secs=0.3)
    starts = []

    class Stamped(BusyMT):
        def translate(self, text, src, tgt):
            starts.append(time.monotonic())
            return super().translate(text, src, tgt)

    mt = Stamped(cpu, secs=0.01)
    done = []

    class Timed(BudgetASR):
        def transcribe(self, audio):
            r = super().transcribe(audio)
            done.append(time.monotonic())
            return r

    asr = Timed(cpu, secs=0.3)
    run(Burst(8), OneSeg(), asr, mt, TextSink(io.StringIO()))
    assert len(starts) >= 2 and starts[1] < done[-1]  # released by the cap, not by the end of input
    assert starts[1] - starts[0] < 0.6  # line 2's cap ran while line 1 was held: not 2 full caps back to back


def test_drafts_yield_too():
    """--stream drafts do not start an MT draft while ASR is behind; the request stays queued."""
    import threading as th

    from tinglongwu.stream import DraftEngine

    d = DraftEngine.__new__(DraftEngine)
    d.final_waiting, d._slot_lock, d._slot = 0, th.Lock(), (1, "привет", "ru")
    d.asr_behind = lambda: True
    assert d.translate_pending() is False and d._slot == (1, "привет", "ru")


def test_gui_wait_line_per_leg():
    """GUI: one leg reporting 0 does not hide the other leg's held lines; the slower backlog is shown."""
    from types import SimpleNamespace

    import pytest

    pytest.importorskip("tkinter")
    from tinglongwu.gui.app import App

    me = SimpleNamespace(_waits={("mtwait", "listen"): 2, ("mtwait", "speak"): 0,
                                 ("backlog", "listen"): 4, ("backlog", "speak"): 1})
    assert App._wait_line(me) == "ASR 4 s behind | MT paused for ASR: 2 line(s) waiting"
    assert App._wait_line(SimpleNamespace(_waits={})) == ""


def test_fast_asr_never_holds_mt():
    """ASR keeping up (no backlog): MT runs at once, nothing reported."""

    class Paced(Burst):
        def frames(self):
            for f in super().frames():
                time.sleep(0.2)
                yield f

    waits = []
    cpu = Cpu()
    mt = BusyMT(cpu, secs=0.01)
    run(Paced(3), OneSeg(), BudgetASR(cpu, secs=0.01), mt, TextSink(io.StringIO()), on_mt_wait=waits.append)
    assert waits == [] and mt.calls == 3


def test_offline_file_clock_does_not_hold():
    """audio_clock (files read faster than real time) keeps the old behaviour: no hold."""
    waits = []
    _drops(True, audio_clock=True, on_mt_wait=waits.append)
    assert waits == []


def test_status_wait_text():
    from tinglongwu.cli import mt_wait_text, status_wait_text

    assert mt_wait_text(0) == "" and mt_wait_text(3) == "MT paused for ASR: 3 line(s) waiting"
    assert status_wait_text(0, 0) == ""
    assert status_wait_text(4, 2) == "ASR 4 s behind | MT paused for ASR: 2 line(s) waiting"
