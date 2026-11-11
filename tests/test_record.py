"""#239: opt-in local recording (--record): default off, WAV only when asked, notice + marker, no sockets."""
import io
import socket
import wave

import numpy as np
import pytest

from tinglongwu import cli
from tinglongwu.capture.base import SAMPLE_RATE, AudioSource
from tinglongwu.record import RecordingSource
from tinglongwu.sink import Result, TextSink


class Fake(AudioSource):
    def frames(self):
        yield np.full(1600, 0.5, dtype=np.float32)
        yield np.zeros(1600, dtype=np.float32)


def test_record_flag_default_off():
    assert cli.parse_args([]).record is None
    assert cli.parse_args(["--record"]).record == ""
    assert cli.parse_args(["--record", "x.wav"]).record == "x.wav"


def test_recording_source_writes_wav_without_sockets(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(socket.socket, "connect", boom)
    path = tmp_path / "t" / "a.wav"
    src = RecordingSource(Fake(), path)
    out = list(src.frames())
    src.close()
    assert len(out) == 2
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, SAMPLE_RATE, 3200)
        first = np.frombuffer(w.readframes(1), dtype="<i2")[0]
    assert first == pytest.approx(16383, abs=2)


def test_text_sink_marks_every_block_only_when_recording():
    r = Result(src="en", original="hello", pivot=None, target="你好", tgt="zh")
    for rec, expect in (("● Recording", True), ("", False)):
        buf = io.StringIO()
        s = TextSink(buf, rec=rec)
        s.write(r)
        s.write(r)
        assert buf.getvalue().count("● Recording") == (2 if expect else 0)


def test_run_session_record_creates_file_and_notice(tmp_path, monkeypatch, capsys):
    """Through run_session with fakes: --record -> WAV + consent notice; no --record -> no audio file."""
    import tinglongwu.cli as c

    captured = {}

    def fake_run(source, *a, **k):
        for _ in source.frames():
            pass
        source.close()
        captured["sink"] = a[3]

    class FakeASR:
        device = "cpu"

    monkeypatch.setattr("tinglongwu.pipeline.run", fake_run)
    monkeypatch.setattr("tinglongwu.asr.FasterWhisperRecognizer", lambda *a, **k: FakeASR())
    monkeypatch.setattr(c, "select_backend", lambda *a, **k: Fake())
    monkeypatch.setattr(c, "models_dir", lambda d=None: tmp_path)
    monkeypatch.setattr(c, "make_translator", lambda *a, **k: None)
    monkeypatch.setenv("TLWU_UI_LANG", "en")
    monkeypatch.chdir(tmp_path)

    c.run_session(c.parse_args(["--transcript-only", "--no-adapt", "--model", "tiny", "--device", "cpu"]))
    assert not list(tmp_path.rglob("*.wav"))
    assert "consent" not in capsys.readouterr().err

    path = tmp_path / "r.wav"
    c.run_session(c.parse_args(["--transcript-only", "--no-adapt", "--model", "tiny", "--device", "cpu",
                                "--record", str(path)]))
    assert path.is_file()
    err = capsys.readouterr().err
    assert "consent" in err and str(path) in err
    assert captured["sink"].rec == "● Recording"
