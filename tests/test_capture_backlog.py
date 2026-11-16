"""#1263: a stalled ASR must not make live capture drop audio.

The real WASAPI capture worker (`WasapiLoopbackSource._worker`) reads 100 ms blocks into a small queue and drops the
oldest block when nobody reads it. Before #1263 the pipeline's segment queue held only 8 segments, so a slow ASR
blocked the capture reader and the worker silently threw audio away. Here ASR stalls for the whole 60 s of fed audio
(time compressed: one block every few ms); afterwards no block may have been lost and every second must reach ASR.
"""
import io
import queue
import threading

import numpy as np

from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.capture._soundcard import BLOCK, NATIVE_RATE
from tinglongwu.capture.base import to_mono_16k
from tinglongwu.capture.wasapi import WasapiLoopbackSource
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.sink import TextSink

FED_S = 60
BLOCKS = FED_S * NATIVE_RATE // BLOCK  # 600 blocks of 100 ms


class _Rec:
    """soundcard recorder stand-in: FED_S seconds of sound, one block every `pace` s, then the device ends."""

    def __init__(self, done: threading.Event, pace: float):
        self.n, self.done, self.pace = 0, done, pace
        self.rng = np.random.default_rng(1263)

    def record(self, numframes):
        self.done.wait(self.pace)
        self.n += 1
        if self.n >= BLOCKS:
            self.done.set()  # last block: the worker loop ends after putting it
        return (0.2 * self.rng.standard_normal((numframes, 2))).astype(np.float32)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeSC:
    def __init__(self, rec):
        self.rec = rec

    def get_microphone(self, _id, include_loopback=True):
        rec = self.rec

        class Mic:
            def recorder(self, samplerate, channels):
                return rec

        return Mic()


class _StalledASR(Recognizer):
    """Blocks until the capture has delivered all FED_S seconds (= ASR 60 s behind), then keeps up."""

    def __init__(self, fed: threading.Event):
        self.fed, self.calls, self.seconds = fed, 0, 0.0

    def transcribe(self, audio):
        assert self.fed.wait(60), "capture never finished"
        self.calls += 1
        self.seconds += len(audio) / 16000
        return Transcript(f"фраза номер {self.calls}", "ru")


class _LiveSource:
    """frames() exactly like WasapiLoopbackSource._auto_frames reads one device: queue.get + to_mono_16k."""

    def __init__(self, src, q, ev):
        self.src, self.q, self.ev = src, q, ev

    def frames(self):
        while True:
            try:
                data = self.q.get(timeout=0.05)
            except queue.Empty:
                if self.ev.is_set():
                    return
                continue
            yield to_mono_16k(data, NATIVE_RATE)

    def close(self):
        pass


def test_asr_stall_loses_no_capture_audio():
    src = WasapiLoopbackSource()
    q, ev = queue.Queue(maxsize=50), threading.Event()  # 5 s of slack: far less than the 60 s stall
    rec = _Rec(ev, pace=0.01)
    worker = threading.Thread(target=src._worker, args=(_FakeSC(rec), "spk", q, ev), daemon=True)
    worker.start()
    asr = _StalledASR(ev)
    run(_LiveSource(src, q, ev), Segmenter(chunk=1.0, use_vad=False, overlap=0.0), asr, None, TextSink(io.StringIO()))
    worker.join(5)
    assert rec.n == BLOCKS
    assert src.lost_seconds == 0.0, f"capture dropped {src.lost_seconds:.1f} s while ASR was stalled"
    assert asr.seconds >= FED_S - 0.5, f"only {asr.seconds:.1f} of {FED_S} s reached ASR"


def test_dropped_capture_blocks_are_counted_and_logged(capsys):
    """When the reader really stops (not ASR: the consumer itself), the drop is counted and logged, never silent."""
    src = WasapiLoopbackSource()
    q, ev = queue.Queue(maxsize=20), threading.Event()
    rec = _Rec(ev, pace=0.0)
    src._worker(_FakeSC(rec), "spk", q, ev)  # nobody reads q: everything past 20 blocks is dropped
    assert src.lost_seconds == (BLOCKS - 20) * BLOCK / NATIVE_RATE
    assert "dropped" in capsys.readouterr().err
