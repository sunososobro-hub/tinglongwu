"""#1681: a GPU model that loads but fails at decode time switches to CPU instead of sitting on "Listening..."."""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest

import tinglongwu.asr as asr


class _Seg:
    text = " hello"
    no_speech_prob = 0.0
    avg_logprob = -0.1
    start, end = 0.0, 1.0


def _install(monkeypatch, calls, cpu_ok=True):
    class FakeModel:
        def __init__(self, path, device="cpu", compute_type="int8", **kw):
            self.device = device
            calls.append((device, compute_type))

        def transcribe(self, audio, **kw):
            if self.device == "cuda" or not cpu_ok:
                raise RuntimeError("Library cublas64_12.dll is not found")
            return iter([_Seg()]), types.SimpleNamespace(language="en")

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    monkeypatch.setattr(asr, "require", lambda p, *a, **k: p)
    monkeypatch.setattr(asr, "resolve_device", lambda choice: "cuda" if choice != "cpu" else "cpu")
    monkeypatch.setattr(asr, "asr_compute_type", lambda dev, *a, **k: "float16" if dev == "cuda" else "int8")
    monkeypatch.setattr(asr, "cpu_fallback_settings", lambda b, t, **k: (2, 4))


AUDIO = np.zeros(16000, dtype=np.float32)


def test_decode_failure_on_cuda_rebuilds_on_cpu_and_retries(monkeypatch, tmp_path, capsys):
    calls = []
    _install(monkeypatch, calls)
    rec = asr.FasterWhisperRecognizer(tmp_path, size="small", device="cuda")
    tr = rec.transcribe(AUDIO)
    assert tr is not None and tr.text == "hello"
    assert rec.device == "cpu" and rec.beam == 2
    assert calls == [("cuda", "float16"), ("cpu", "int8")]
    err = capsys.readouterr().err
    assert err.count("GPU 無法使用，已改用 CPU：Library cublas64_12.dll is not found") == 1
    rec.transcribe(AUDIO)  # no second rebuild, no second line
    assert len(calls) == 2 and "已改用 CPU" not in capsys.readouterr().err


def test_cpu_retry_failure_propagates(monkeypatch, tmp_path):
    calls = []
    _install(monkeypatch, calls, cpu_ok=False)
    rec = asr.FasterWhisperRecognizer(tmp_path, size="small", device="cuda")
    with pytest.raises(RuntimeError):
        rec.transcribe(AUDIO)
    assert rec.device == "cpu"


def test_cpu_recognizer_error_is_not_swallowed(monkeypatch, tmp_path):
    calls = []
    _install(monkeypatch, calls, cpu_ok=False)
    rec = asr.FasterWhisperRecognizer(tmp_path, size="small", device="cpu")
    with pytest.raises(RuntimeError):
        rec.transcribe(AUDIO)
    assert calls == [("cpu", "int8")]
