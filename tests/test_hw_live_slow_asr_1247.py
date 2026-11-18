"""#1247: the #894 live-mic diagnose names slow ASR calls (from tlwu --debug-latency) and the GPU's other users, so a
14.5 s subtitle gap made of one 5.6 s ASR call can be told apart as a decode problem or a shared GPU."""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import numpy as np

from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "hw_live_checks.py"


def _load():
    spec = importlib.util.spec_from_file_location("hw_live_checks_slow_1247", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


hl = _load()


class _Frames:
    def __init__(self, n):
        self.n = n

    def frames(self):
        for _ in range(self.n):
            yield np.full(16000, 0.1, np.float32)

    def close(self):
        pass


class _SlowSecondASR(Recognizer):
    """Second call sleeps `slow` seconds (the stalled call); the others return at once."""

    def __init__(self, slow):
        self.n, self.slow = 0, slow

    def transcribe(self, audio):
        self.n += 1
        if self.n == 2:
            time.sleep(self.slow)
        return Transcript(f"line {self.n}", "en")


class _Sink:
    def __init__(self):
        self.rows = []

    def write(self, r):
        self.rows.append(r.original)


def test_slow_asr_calls_reads_the_real_pipeline_debug_lines():
    """Runs the real pipeline with its --debug-latency log; the stalled call (and only it) is reported."""
    logs: list[str] = []
    run(_Frames(3), Segmenter(chunk=1.0, use_vad=False), _SlowSecondASR(0.3), None, _Sink(), "en", log=logs.append)
    slow = hl.slow_asr_calls(logs, min_s=0.25)
    assert len(slow) == 1 and "(call 2 of " in slow[0], (slow, logs)
    assert hl.slow_asr_calls(logs, min_s=5.0) == []


def test_slow_asr_calls_reads_draft_stripped_lines():
    """With --stream drafts the pipeline logs without the `latency: ` prefix; still parsed."""
    lines = ["asr 640 ms for 7.9 s of audio (0 line(s) waiting for MT)",
             "latency: asr 5600 ms for 8.0 s of audio (0 line(s) waiting for MT)", "tlwu: ASR caught up"]
    assert hl.slow_asr_calls(lines) == ["5.6 s for 8.0 s audio (call 2 of 2)"]


def test_parse_gpu_apps_keeps_names_not_paths():
    out = ("C:\\Users\\owner\\AppData\\Local\\tinglongwu\\python\\python.exe, 1530 MiB\n"
           "C:\\Program Files\\Game\\game.exe, 2200 MiB\n")
    assert hl.parse_gpu_apps(out) == ["python.exe 1530 MiB", "game.exe 2200 MiB"]
    assert hl.parse_gpu_apps("No running processes found\n") == []
    assert hl.parse_gpu_apps("") == []


def test_gpu_apps_without_nvidia_smi_is_empty(monkeypatch):
    def missing_tool(*args, **kwargs):  # PATH="" is not enough on Windows: System32 is searched first
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(hl.subprocess, "run", missing_tool)
    assert hl.gpu_apps() == []


def test_gpu_sampler_keeps_an_app_that_came_and_went_during_play():
    """A GPU app present only in the middle samples is still reported once, by name, after stop()."""
    snaps = iter([["python.exe 1500 MiB"], ["python.exe 1500 MiB", "game.exe 2200 MiB"], ["python.exe 1600 MiB"]])

    def probe():
        return next(snaps, ["python.exe 1600 MiB"])

    with hl.GpuSampler(every=0.01, probe=probe) as s:
        deadline = time.monotonic() + 5
        while len(s.seen) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    assert s.seen == ["python.exe", "game.exe"]
    assert not s._t.is_alive()


def test_gpu_sampler_drops_a_poll_that_ends_after_play():
    """A poll still running when play ends (signal()) is not reported as seen during play."""
    import threading

    started, release = threading.Event(), threading.Event()

    def probe():
        started.set()
        release.wait(5)
        return ["late.exe 100 MiB"]

    s = hl.GpuSampler(every=0.01, probe=probe)
    with s:
        assert started.wait(5)
        s.signal()
        release.set()
    assert s.seen == []


def test_live_mic_accepts_debug_latency_with_latency_jsonl(tmp_path):
    """The flags the check now passes to the live run parse together (no parser error)."""
    from tinglongwu.cli import parse_args

    a = parse_args(["--mic", "--capture-device", "CABLE Output", "--latency-jsonl", str(tmp_path / "l.jsonl"),
                    "--debug-latency", "--lang", "en", "--transcript-only"])
    assert a.debug_latency and a.latency_jsonl and a.mic
