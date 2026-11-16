"""#1263: a stalled ASR must not make live capture drop audio.

The real WASAPI capture worker (`WasapiLoopbackSource._worker`) reads 100 ms blocks into a small queue and drops the
oldest block when nobody reads it. Before #1263 the pipeline's segment queue held only 8 segments, so a slow ASR
blocked the capture reader and the worker silently threw audio away. Here ASR stalls for the whole 60 s of fed audio
(time compressed: one block every few ms); afterwards no block may have been lost and every second must reach ASR.
"""
import io
import queue
import threading
import time

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
READER_STUCK_S = 3.0  # a reader that frees no queue slot for this long is blocked, not slow


class _Rec:
    """soundcard recorder stand-in: FED_S seconds of sound, then the device ends.

    Time is compressed, so the test must not depend on how fast the CI machine is: a real device delivers the next
    block regardless, but here record() first waits (up to READER_STUCK_S) for the reader to make room in the
    worker queue. A reader that is merely slow is waited for; a reader that does not move for READER_STUCK_S is
    blocked (pre-#1263: by the ASR stall), and from then on blocks arrive back to back and the worker drops them.
    """

    def __init__(self, done: threading.Event, q: queue.Queue | None = None):
        self.n, self.done, self.q, self.stuck = 0, done, q, False
        self.rng = np.random.default_rng(1263)

    def record(self, numframes):
        if self.q is not None and not self.stuck:
            t_end = time.monotonic() + READER_STUCK_S
            while self.q.full() and time.monotonic() < t_end:
                time.sleep(0.001)
            self.stuck = self.q.full()
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

    def __init__(self, src, q, worker: threading.Thread):
        self.src, self.q, self.worker = src, q, worker

    def frames(self):
        while True:
            try:
                data = self.q.get(timeout=0.05)
            except queue.Empty:
                # ends only after the worker exited (its final put is done) AND the queue is drained
                if not self.worker.is_alive() and self.q.empty():
                    return
                continue
            yield to_mono_16k(data, NATIVE_RATE)

    def close(self):
        pass


def test_asr_stall_loses_no_capture_audio():
    src = WasapiLoopbackSource()
    q, ev = queue.Queue(maxsize=20), threading.Event()  # the real worker queue size (2 s)
    rec = _Rec(ev, q)
    worker = threading.Thread(target=src._worker, args=(_FakeSC(rec), "spk", q, ev), daemon=True)
    worker.start()
    asr = _StalledASR(ev)
    run(_LiveSource(src, q, worker), Segmenter(chunk=1.0, use_vad=False, overlap=1 / 16000), asr, None,
        TextSink(io.StringIO()))
    worker.join(5)
    assert rec.n == BLOCKS
    assert src.lost_seconds == 0.0, f"capture dropped {src.lost_seconds:.1f} s while ASR was stalled"
    # overlap of 1 sample (overlap=0 keeps the whole buffer: buf[-0:]); each segment repeats at most that one sample
    assert abs(asr.seconds - FED_S) < 0.01, f"{asr.seconds:.3f} s reached ASR, expected the {FED_S} s fed"


def test_dropped_capture_blocks_are_counted_and_logged(capsys):
    """When the reader really stops (not ASR: the consumer itself), the drop is counted and logged, never silent."""
    src = WasapiLoopbackSource()
    q, ev = queue.Queue(maxsize=20), threading.Event()
    rec = _Rec(ev)
    src._worker(_FakeSC(rec), "spk", q, ev)  # nobody reads q: everything past 20 blocks is dropped
    assert src.lost_seconds == (BLOCKS - 20) * BLOCK / NATIVE_RATE
    assert "dropped" in capsys.readouterr().err
