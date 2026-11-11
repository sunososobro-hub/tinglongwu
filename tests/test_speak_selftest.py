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
