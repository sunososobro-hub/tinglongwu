"""#205: scoring of the speak-mode self-test (behaviour on synthetic wavs; no audio device, no models)."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

sf = pytest.importorskip("soundfile")
_spec = importlib.util.spec_from_file_location(
    "speak_selftest", Path(__file__).resolve().parent.parent / "scripts" / "speak_selftest.py")
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)

SR = 24000


def bursts(seconds, level_dbfs, sr=SR, on=1.0, off=0.5):
    """Sine bursts (on s tone, off s silence) for `seconds` total, RMS of the tone = level_dbfs."""
    t = np.arange(int(sr * seconds)) / sr
    amp = 10 ** (level_dbfs / 20) * np.sqrt(2)
    return (amp * np.sin(2 * np.pi * 220 * t) * ((t % (on + off)) < on)).astype(np.float32)


def wav(tmp_path, name, x, sr=SR):
    p = tmp_path / name
    sf.write(str(p), x, sr)
    return str(p)


def test_identical_recording_passes(tmp_path):
    x = bursts(20, -20)
    r = st.score_wavs(wav(tmp_path, "t.wav", x), wav(tmp_path, "r.wav", np.repeat(x, 2), 48000))
    assert r["verdict"] == "PASS" and r["recorded_ratio"] >= 0.99
    assert r["recorded"]["rms_dbfs"] == pytest.approx(-20, abs=1)


def test_truncated_recording_fails(tmp_path):
    x = bursts(20, -20)
    r = st.score_wavs(wav(tmp_path, "t.wav", x), wav(tmp_path, "r.wav", x[: len(x) // 2]))
    assert r["verdict"] == "FAIL" and r["recorded_ratio"] == pytest.approx(0.5, abs=0.05)


def test_quiet_recording_fails_on_rms(tmp_path):
    x = bursts(20, -20)
    r = st.score_wavs(wav(tmp_path, "t.wav", x), wav(tmp_path, "r.wav", bursts(20, -40)))
    assert r["verdict"] == "FAIL" and r["recorded_ratio"] >= 0.9 and r["recorded"]["rms_dbfs"] < -35


def test_silent_recording_fails(tmp_path):
    r = st.score_wavs(wav(tmp_path, "t.wav", bursts(10, -20)), wav(tmp_path, "r.wav", np.zeros(SR * 10, np.float32)))
    assert r["verdict"] == "FAIL"


def test_no_tts_output_fails():
    assert st.score(0.0, st.speech_stats(bursts(5, -20), SR))["verdict"] == "FAIL"


def test_threshold_boundary():
    rec = {"speech_s": 9.0, "rms_dbfs": -30.0}
    assert st.score(10.0, rec)["verdict"] == "PASS"
    assert st.score(10.0, {**rec, "speech_s": 8.9})["verdict"] == "FAIL"


def test_parse_log_reads_timing_lines():
    log = ("   [vad 0.60 | stt 1.10 | mt 0.20 | tts 0.80 | total 2.70 s]\n"
           "latency asr=1.1s mt=1.3s first_audio=2.1s done=3.0s\n"
           "   [vad 0.60 | stt 0.90 | mt 0.30 | tts - | total 1.80 s]\n"
           "latency asr=0.9s mt=1.2s first_audio=-s done=1.2s\n")
    r = st.parse_log(log)
    assert r["sentences_tts"] == 1 and len(r["latencies"]) == 2
    assert r["latencies"][0] == {"vad": 0.6, "stt": 1.1, "mt": 0.2, "tts": 0.8, "total": 2.7}
    assert r["latencies"][1]["tts"] is None


def _args(**kw):
    import argparse
    d = dict(to="en", mt="auto", whisper=None, models_dir=None, tts_python=None, tts_model=None)
    return argparse.Namespace(**{**d, **kw})


def test_linux_records_tlwu_mic_and_uses_pipeline_vmic():
    assert st.record_name("linux") == "tlwu_mic"
    assert st.record_name("win32") == "CABLE Output"
    lin = st._proto_args(_args(), "linux")
    assert "--device" not in lin and "--allow-monitor" not in lin and "--no-vmic" not in lin
    win = st._proto_args(_args(), "win32")
    assert win[win.index("--device") + 1] == "CABLE Input" and "--allow-monitor" in win


def test_linux_default_kokoro_python_is_posix_path(tmp_path, monkeypatch):
    py = tmp_path / "tts-kokoro" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    monkeypatch.setattr(st.Path, "home", classmethod(lambda cls: tmp_path))
    a = st._proto_args(_args(), "linux")
    assert a[a.index("--tts-python") + 1] == str(py)


def test_main_no_longer_refuses_linux(tmp_path, capsys):
    if not __import__("sys").platform.startswith("linux"):
        pytest.skip("linux only")
    assert st.main(["--clip", str(tmp_path / "missing.wav")]) == 1
    assert "clip not found" in capsys.readouterr().err


def test_run_child_dies_before_listening_reports_child_error(tmp_path, monkeypatch):
    """#474: the recorder is never started when the child exits before 'listening'; run() must not raise
    'cannot join thread before it is started' and must report the child's last lines instead."""
    import argparse
    import subprocess
    class FakeP:
        def __init__(self, *a, **k):
            self.stdout = iter([b"compute device: cuda\n", b"ModelNotFound: Whisper model missing\n"])

        def wait(self, timeout=None):
            return 1

    monkeypatch.setattr(subprocess, "Popen", FakeP)
    monkeypatch.setattr(st, "TAIL_S", 0.0)
    clip = tmp_path / "zh.wav"
    clip.write_bytes(b"")
    a = argparse.Namespace(clip=str(clip), to="ru", mt="auto", whisper=None, models_dir=None, tts_python=None,
                           tts_model=None, timeout=5, keep_log=None, keep_wav=None)
    o = st.run(a)
    assert o["verdict"] == "FAIL"
    assert any("ModelNotFound" in r for r in o["reasons"])
