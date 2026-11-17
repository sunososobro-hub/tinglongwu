"""#1369: on a CPU that cannot keep up, live subtitles stay close to the present (bounded lag, stated catch-up policy)
instead of drifting further behind (owner field test: ~33 s behind after 40 s of video, growing)."""
import collections
import math
import queue
import re
import sys
import threading
import time
import types

import numpy as np
import pytest

from tinglongwu import pipeline
from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.hallucination import is_hallucination
from tinglongwu.i18n import t as _msg
from tinglongwu.merge import Unit
from tinglongwu.pipeline import run
from tinglongwu.sink import Sink
from tinglongwu.translate import Translation, Translator

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


class VirtualClock:
    """Simulated time for run() and its threads, so the lag below does not depend on how busy the machine is.

    The lag test first measured wall time (1 simulated second = 100 ms): a busy CI runner added scheduling delay and
    failed it at 10.7 s against a 10 s bound. Here only one of the simulation's threads runs at a time; time moves
    only when every one of them waits (sleep, queue get, event wait, join), straight to the earliest deadline, and
    waiting threads resume in a fixed order (oldest wait first). The same run gives the same numbers on any machine.
    Patches pipeline.time / queue / threading; real locks stay (never held across a wait)."""

    def __init__(self):
        self.now = 0.0
        self.cond = threading.Condition()
        self.waiters = []  # [seq, ready(), deadline]
        self.seq = 0
        self.turn = None  # the waiter entry allowed to run; the test thread runs while it is None or taken

    def monotonic(self):
        return self.now

    def _entry(self, ready, deadline):
        e = [self.seq, ready, math.inf if deadline is None else deadline]
        self.seq += 1
        self.waiters.append(e)
        return e

    def _next(self):
        """The running thread waits or ends: hand the turn to the oldest ready waiter, moving time if none is."""
        if not self.waiters:
            self.turn = None
            return
        ready = [w for w in self.waiters if w[2] <= self.now or w[1]()]
        if not ready:
            t = min(w[2] for w in self.waiters)
            if t == math.inf:
                raise RuntimeError("virtual clock: every thread waits with no deadline")
            self.now = t
            ready = [w for w in self.waiters if w[2] <= self.now]
        self.turn = min(ready, key=lambda w: w[0])
        self.cond.notify_all()

    def _park(self, e):
        while self.turn is not e:
            self.cond.wait()
        self.waiters.remove(e)

    def wait(self, ready, timeout=None):
        with self.cond:
            e = self._entry(ready, None if timeout is None else self.now + timeout)
            self._next()
            self._park(e)
        return ready()

    def sleep(self, s):
        self.wait(lambda: False, max(0.0, s))

    def patch(self, monkeypatch):
        clock = self

        class Event:
            def __init__(self):
                self.flag = False

            def set(self):
                self.flag = True

            def clear(self):
                self.flag = False

            def is_set(self):
                return self.flag

            def wait(self, timeout=None):
                return self.flag or clock.wait(lambda: self.flag, timeout)

        class Queue:
            def __init__(self, maxsize=0):
                self.items = collections.deque()  # unbounded: live capture never blocks on put (#1263)

            def put(self, item, block=True, timeout=None):
                self.items.append(item)

            def put_nowait(self, item):
                self.items.append(item)

            def get(self, block=True, timeout=None):
                if not self.items and block:
                    clock.wait(lambda: bool(self.items), timeout)
                if not self.items:
                    raise queue.Empty
                return self.items.popleft()

            def get_nowait(self):
                return self.get(False)

            def qsize(self):
                return len(self.items)

        class Thread:
            def __init__(self, target, args=(), kwargs=None, daemon=None):
                self.target, self.args, self.kwargs, self.done = target, args, kwargs or {}, False
                self.t = threading.Thread(target=self._run, daemon=True)

            def start(self):
                with clock.cond:
                    self.e = clock._entry(lambda: True, None)  # runs when the starting thread next waits
                self.t.start()

            def _run(self):
                with clock.cond:
                    clock._park(self.e)
                try:
                    self.target(*self.args, **self.kwargs)
                finally:
                    with clock.cond:
                        self.done = True
                        clock._next()

            def join(self, timeout=None):
                if not self.done:
                    clock.wait(lambda: self.done, timeout)

            def is_alive(self):
                return not self.done

        monkeypatch.setattr(pipeline, "time", types.SimpleNamespace(monotonic=self.monotonic, sleep=self.sleep))
        monkeypatch.setattr(pipeline, "queue", types.SimpleNamespace(Queue=Queue, Empty=queue.Empty, Full=queue.Full))
        monkeypatch.setattr(pipeline, "threading", types.SimpleNamespace(Thread=Thread, Event=Event,
                                                                          Lock=threading.Lock))
        self.Thread, self.Event = Thread, Event


class Clip:
    """Live source: segment i (SEG_S of audio) is complete at t0 + (i + 1) * SEG_S."""

    def __init__(self, n, clock):
        self.n, self.t_end, self.clock = n, {}, clock

    def frames(self):
        t0 = self.clock.monotonic()
        for i in range(self.n):
            self.clock.sleep(t0 + (i + 1) * SEG_S - self.clock.monotonic())
            self.t_end[i] = self.clock.monotonic()
            yield np.full(int(SEG_S * SR), _val(i), dtype="float32")

    def close(self):
        pass


class OneSeg:
    def segments(self, frames):
        yield from frames


class Cpu:
    def __init__(self):
        self.mt_busy = 0


class SlowASR(Recognizer):
    def __init__(self, cpu, clock=None):
        self.cpu, self.calls, self.clock = cpu, 0, clock

    def transcribe(self, audio):
        busy = BUSY if self.cpu.mt_busy else 1.0
        self.clock.sleep((ASR_FIX + ASR_PER_S * len(audio) / SR) * busy)
        self.calls += 1
        return Transcript(_text(_idx_of(audio)), "en")


class SlowMT(Translator):
    def __init__(self, cpu, clock):
        self.cpu, self.clock = cpu, clock

    def translate(self, text, src, tgt):
        self.cpu.mt_busy += 1
        try:
            self.clock.sleep(MT_S)
        finally:
            self.cpu.mt_busy -= 1
        return Translation(None, "譯:" + text)


class Echo(Translator):
    def translate(self, text, src, tgt):
        return Translation(None, "譯:" + text)


class Shown(Sink):
    def __init__(self, clock=time.monotonic):
        self.lines = []  # (time, Result)
        self.lock = threading.Lock()
        self.clock = clock

    def write(self, r):
        with self.lock:
            self.lines.append((self.clock(), r))


def _simulate(monkeypatch, old=False):
    if old:  # the policy before #1369: 30 s hold, no catch-up
        monkeypatch.setattr(pipeline, "MT_HOLD_MAX", 30.0)
        for name in ("ASR_JOIN_S", "MT_STALE_S"):
            monkeypatch.setattr(pipeline, name, float("inf"))
    clock = VirtualClock()
    clock.patch(monkeypatch)
    cpu = Cpu()
    asr = SlowASR(cpu, clock)
    src, sink, notes = Clip(int(CLIP_S / SEG_S), clock), Shown(clock.monotonic), []
    stop = clock.Event()
    th = clock.Thread(target=run, args=(src, OneSeg(), asr, SlowMT(cpu, clock), sink, "zh"),
                      kwargs={"stop_event": stop, "on_catchup": notes.append})
    t0 = clock.monotonic()
    th.start()
    clock.sleep(CLIP_S + 0.1)
    stop.set()
    th.join(5)
    assert not th.is_alive()
    clock.sleep(2 * SEG_S)  # let the capture thread see Stop and end

    def age(at_s):
        """How far behind the newest translated subtitle on screen is at `at_s` seconds into the clip
        (checkpoint minus the end of its newest segment)."""
        at = t0 + at_s
        shown = [max(_idx(r.original)) for when, r in sink.lines if when <= at and r.target.startswith("譯:")]
        assert shown, f"no translated line by {at_s} s"
        return at - src.t_end[max(shown)]

    def lag(at_s):
        """Subtitle lag at `at_s`: the on-screen age averaged over the 20 s before it (1 s steps). Lines come out
        once per ~3 s segment, so a single instant swings by up to one segment."""
        return sum(age(at_s - k) for k in range(20)) / 20

    return lag(60), lag(CLIP_S), sink, notes, [asr.calls]


def test_lag_stays_bounded_on_a_cpu_that_cannot_keep_up(monkeypatch):
    lag60, lag180, sink, notes, calls = _simulate(monkeypatch)
    # floor: 3 s segment + one ~3.3 s ASR call + 1 s MT + about half a line interval on screen = ~7 s.
    # The issue's <= 8 s is for the low-end runner; here the bound is that floor plus a margin, and no growth.
    assert lag60 <= 10.0, (lag60, lag180)
    assert lag180 - lag60 <= 2.0, (lag60, lag180)
    assert calls[0] < CLIP_S / SEG_S - 5, "waiting segments were transcribed together"


def test_old_policy_drifts(monkeypatch):
    """The same machine with the pre-#1369 policy: translations fall far behind and the gap grows (the field test)."""
    lag60, lag180, _, _, _ = _simulate(monkeypatch, old=True)
    assert lag60 > 8.0 or lag180 - lag60 > 2.0, (lag60, lag180)


def test_simulation_is_deterministic(monkeypatch):
    """#1369 flake: the lag comes from simulated time only, so two runs give the same numbers to the last bit."""
    a = _simulate(monkeypatch)[:2]
    b = _simulate(monkeypatch)[:2]
    assert a == b, (a, b)


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


class QuietSeg:
    """Live VAD shape: a silence notice (_Quiet) after every segment, as segment.on_quiet sends during pauses."""

    on_quiet = None

    def segments(self, frames):
        for f in frames:
            yield f
            if self.on_quiet is not None:
                self.on_quiet(0.5)


class QuietMerger:
    """Merger that releases each segment's text on the next silence notice (records the order it sees)."""

    deadline = None

    def __init__(self):
        self.buf, self.seen = [], []

    def add(self, text, lang, now, **kw):
        self.seen.append("text")
        self.buf.append(text)
        return []

    def quiet(self, seconds, now):
        self.seen.append("quiet")
        out, self.buf = [Unit(" ".join(self.buf), "en", False, None)] if self.buf else [], []
        return out

    def due(self, now):
        return []

    def flush(self):
        return self.quiet(0, 0)


def test_join_passes_silence_notices(monkeypatch):
    """Pauses between segments (a _Quiet after each) do not stop the join; every word still comes out in order."""
    monkeypatch.setattr(pipeline, "ASR_JOIN_S", 0.05)
    asr, sink, merger = StallASR(0.2), Shown(), QuietMerger()
    run(Burst(12), QuietSeg(), asr, Echo(), sink, "zh", merger=merger)
    assert len(asr.sizes) < 6 and max(asr.sizes) > 1.0, asr.sizes
    assert _heard(sink) == list(range(12))
    assert merger.seen[0] == "text" and "quiet" in merger.seen


def test_silent_segment_does_not_hide_a_quiet_one_in_a_join(monkeypatch):
    """A segment just above SILENCE_RMS joined with digital silence still reaches ASR (the silence is left out)."""
    from tinglongwu.hallucination import SILENCE_RMS

    monkeypatch.setattr(pipeline, "ASR_JOIN_S", 0.05)

    class Mixed(Burst):
        def frames(self):
            yield np.full(SR, _val(0), dtype="float32")
            yield np.full(SR, SILENCE_RMS * 1.1, dtype="float32")
            for _ in range(3):
                yield np.zeros(SR, dtype="float32")

    class Rec(StallASR):
        def transcribe(self, audio):
            if not self.sizes:
                time.sleep(self.stall)
            self.sizes.append(len(audio) / SR)
            return Transcript("quiet words", "en") if len(self.sizes) == 2 else Transcript("n0.", "en")

    asr, sink = Rec(0.2), Shown()
    run(Mixed(5), OneSeg(), asr, None, sink, "zh")
    assert asr.sizes == [1.0, 1.0]  # the quiet segment alone; the 3 silent ones were left out, not joined in
    assert [r.original for _, r in sink.lines] == ["n0.", "quiet words"]


def test_recorded_start_of_a_join_is_the_first_kept_segment(monkeypatch):
    """--record: a joined segment is saved from where its first kept segment starts, not from end minus joined length
    (the silent segments left out of the join would otherwise shift the start later)."""
    monkeypatch.setattr(pipeline, "ASR_JOIN_S", 0.05)

    class Mixed(Burst):
        def frames(self):
            yield np.full(SR, _val(0), dtype="float32")  # 0-1 s, transcribed alone (first call stalls)
            yield np.full(SR, _val(1), dtype="float32")  # 1-2 s
            yield np.zeros(SR, dtype="float32")  # 2-3 s: silence, left out of the join
            yield np.full(SR, _val(3), dtype="float32")  # 3-4 s

    class Rec:
        def __init__(self):
            self.rows = []

        def add(self, n, seg, start, end):
            self.rows.append((round(start, 3), round(end, 3), len(seg) / SR))

        def take(self, first, last):
            return None

        def line(self, clip, r):
            pass

    rec = Rec()
    run(Mixed(4), OneSeg(), StallASR(0.2), None, Shown(), "zh", recorder=rec)
    assert rec.rows[0] == (0.0, 1.0, 1.0)
    assert rec.rows[1] == (1.0, 4.0, 2.0)  # 1 s .. 4 s on the audio clock, 2 s of kept speech


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
                                  "音樂。音樂。", "Музыка", "[soft instrumental background music]", "[♪♪]"])
def test_music_only_segment_produces_no_line(text):
    assert is_hallucination(text, 0.1, 0.0)


@pytest.mark.parametrize("text", ["I love music.", "♪ I love you ♪", "[Music] Welcome back everyone", "[Applause]",
                                  "(I love music.)", "[We all love this music]", "(we love this music)",
                                  "(I love music)", "[play the music]"])
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
