"""#1198: the MT bench's background Whisper can run like live audio (each clip once its audio has arrived) instead
of back to back. Fake recognizer + fake clock: no models needed."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("mt_cpu_threads", ROOT / "scripts/bench/mt_cpu_threads.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _run(paced: bool, clip_s: float, asr_s: float, n: int):
    """Run AsrLoad._loop on a fake clock until `n` clips are done; return the (start, end) spans and the waits."""
    clock, waits = Clock(), []
    load = None

    class Asr:
        def transcribe(self, audio):
            clock.t += asr_s
            if len(load.spans) + 1 >= n:
                load.stop.set()

    def wait(sec):
        waits.append(round(sec, 6))
        clock.t += sec
        return load.stop.is_set()

    audio = [np.zeros(int(clip_s * bench.SAMPLE_RATE), dtype=np.float32)]
    load = bench.AsrLoad(None, "small", 2, [], paced=paced, asr=Asr(), audio=audio, clock=clock, wait=wait)
    load._loop()
    assert load.error is None
    return load.spans, waits


def test_busy_load_runs_back_to_back():
    spans, waits = _run(paced=False, clip_s=5.0, asr_s=2.0, n=3)
    assert spans == [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)]
    assert waits == []


def test_live_load_waits_for_the_audio():
    spans, waits = _run(paced=True, clip_s=5.0, asr_s=2.0, n=3)
    # clip i starts when its audio has arrived: 5, 10, 15 s
    assert spans == [(5.0, 7.0), (10.0, 12.0), (15.0, 17.0)]
    assert waits == [5.0, 3.0, 3.0]


def test_live_load_slower_than_real_time_runs_back_to_back():
    spans, waits = _run(paced=True, clip_s=1.0, asr_s=3.0, n=3)
    assert spans == [(1.0, 4.0), (4.0, 7.0), (7.0, 10.0)]  # lags: no idle once behind
    assert waits == [1.0]


def test_busy_share():
    load = bench.AsrLoad(None, "small", 1, [], asr=object(), audio=[np.zeros(1)])
    load.spans = [(0.0, 2.0), (5.0, 7.0), (10.0, 12.0)]
    assert load.busy(0.0, 10.0) == 0.4
    assert load.busy(1.0, 6.0) == 0.4  # partial overlaps count
    assert load.busy(3.0, 3.0) == 0.0


def test_cli_rejects_unknown_load(capsys):
    import pytest

    with pytest.raises(SystemExit):
        bench.main(["--asr-load", "nonsense"])
