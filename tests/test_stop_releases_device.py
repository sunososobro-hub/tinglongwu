"""GUI Stop must release the capture device promptly, even when nothing but silence is being captured."""
import io
import sys
import threading
import time
import types

import numpy as np

from tinglongwu.capture._soundcard import BLOCK, SoundcardSource
from tinglongwu.capture.base import AudioSource
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.sink import TextSink


class SilentLiveSource(AudioSource):
    """Never-ending capture of silence (blocks ~100 ms like a real recorder); records close()."""

    def __init__(self):
        self.closed = threading.Event()
        self.frames_ended = threading.Event()

    def frames(self):
        try:
            while not self.closed.is_set():
                time.sleep(0.05)
                yield np.zeros(1600, np.float32)
        finally:
            self.frames_ended.set()

    def close(self):
        self.closed.set()


class NoASR:
    def transcribe(self, audio):
        raise AssertionError("silence must not reach ASR")


def test_stop_in_silence_returns_fast_and_closes():
    src = SilentLiveSource()
    stop = threading.Event()
    th = threading.Thread(target=lambda: run(src, Segmenter(chunk=1.0, use_vad=False), NoASR(), None,
                                             TextSink(io.StringIO()), stop_event=stop), daemon=True)
    th.start()
    time.sleep(0.5)
    t0 = time.monotonic()
    stop.set()
    th.join(timeout=3)
    assert not th.is_alive()
    assert time.monotonic() - t0 < 1.5
    assert src.closed.is_set()
    assert src.frames_ended.wait(1.0)  # capture generator really ended


def test_soundcard_source_close_releases_recorder(monkeypatch):
    events = []

    class Rec:
        def __enter__(self):
            events.append("open")
            return self

        def __exit__(self, *a):
            events.append("release")

        def record(self, numframes):
            time.sleep(0.02)
            return np.zeros((numframes, 2), np.float32)

    class Mic:
        def recorder(self, **kw):
            return Rec()

    fake = types.ModuleType("soundcard")
    monkeypatch.setitem(sys.modules, "soundcard", fake)

    class Src(SoundcardSource):
        def _pick_microphone(self, sc):
            return Mic()

    src = Src()
    it = src.frames()
    assert next(it).dtype == np.float32
    src.close()
    t0 = time.monotonic()
    assert list(it) == []
    assert time.monotonic() - t0 < 1.0
    assert events == ["open", "release"]
    assert BLOCK > 0


def _recording_source(events):
    """A SoundcardSource whose fake recorder logs open/release (no soundcard library needed)."""

    class Rec:
        def __enter__(self):
            events.append("open")
            return self

        def __exit__(self, *a):
            events.append("release")

        def record(self, numframes):
            time.sleep(0.01)
            return np.zeros((numframes, 2), np.float32)

    class Mic:
        def recorder(self, **kw):
            return Rec()

    class Src(SoundcardSource):
        def _pick_microphone(self, sc):
            return Mic()

    return Src()


class BrokenSegmenter:
    """Pulls a couple of capture frames, then fails (e.g. the VAD model errors out)."""

    def segments(self, frames):
        for i, _ in enumerate(frames):
            if i == 2:
                raise RuntimeError("vad failed")
        yield from ()


def test_capture_error_in_segmenter_still_releases_device(monkeypatch):
    """#10: when the capture thread dies with an error the GUI shows the error and 'stopped'; by then the
    recorder must be closed. Before the fix it was only closed when the cyclic garbage collector freed the
    exception's traceback (which pins the suspended capture generator), so the mic stayed in use."""
    import gc

    import pytest

    monkeypatch.setitem(sys.modules, "soundcard", types.ModuleType("soundcard"))
    events = []
    src = _recording_source(events)
    gc.disable()  # make "released only by the cyclic GC" deterministic
    try:
        with pytest.raises(RuntimeError, match="vad failed"):
            run(src, BrokenSegmenter(), NoASR(), None, TextSink(io.StringIO()), stop_event=threading.Event())
        assert events == ["open", "release"]
    finally:
        gc.enable()


def test_stop_release_happens_on_capture_thread_before_run_returns(monkeypatch):
    """Stop in silence: the recorder is released (by the thread that opened it, which owns the COM scope on
    Windows) before run() returns, so the GUI's 'stopped' means the mic indicator is already off."""
    import gc

    monkeypatch.setitem(sys.modules, "soundcard", types.ModuleType("soundcard"))
    events = []
    src = _recording_source(events)
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    gc.disable()
    try:
        run(src, Segmenter(chunk=1.0, use_vad=False), NoASR(), None, TextSink(io.StringIO()), stop_event=stop)
        assert events == ["open", "release"]
    finally:
        gc.enable()
