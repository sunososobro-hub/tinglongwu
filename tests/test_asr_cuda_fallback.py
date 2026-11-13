"""#600: a CUDA init failure in ASR falls back to CPU with one log line and never crashes."""
from __future__ import annotations

import sys
import types

import pytest

import tinglongwu.asr as asr


def _install_fake_whisper(monkeypatch, calls):
    class FakeModel:
        def __init__(self, path, device="cpu", compute_type="int8", **kw):
            calls.append((device, compute_type))
            if device == "cuda":
                raise RuntimeError("CUDA driver version is insufficient")

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)


def test_cuda_init_failure_falls_back_to_cpu(monkeypatch, tmp_path, capsys):
    calls = []
    _install_fake_whisper(monkeypatch, calls)
    monkeypatch.setattr(asr, "require", lambda p, *a, **k: p)
    monkeypatch.setattr(asr, "resolve_device", lambda choice: "cuda")
    monkeypatch.setattr(asr, "asr_compute_type", lambda dev, *a, **k: "float16" if dev == "cuda" else "int8")

    rec = asr.FasterWhisperRecognizer(tmp_path, size="small", device="cuda")

    assert rec.device == "cpu"
    assert [c[0] for c in calls] == ["cuda", "cpu"]
    err = capsys.readouterr().err
    assert err.count("falling back to CPU") == 1


def test_cpu_failure_is_not_swallowed(monkeypatch, tmp_path):
    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("bad model")

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = Boom
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    monkeypatch.setattr(asr, "require", lambda p, *a, **k: p)
    monkeypatch.setattr(asr, "resolve_device", lambda choice: "cpu")
    with pytest.raises(RuntimeError):
        asr.FasterWhisperRecognizer(tmp_path, size="small", device="cpu")
