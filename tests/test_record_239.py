"""#239 --record: off by default; one WAV per final line + a matching segments.jsonl row; "● Recording" mark; no net."""
import io
import json
import socket
import wave

import numpy as np

from tinglongwu import cli
from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.merge import SentenceMerger
from tinglongwu.pipeline import run
from tinglongwu.sink import JsonlSaveSink, TextSink
from tinglongwu.translate import Translation, Translator

SR = 16000
TEXTS = ["Hello there my good friend", "how are you today.", "I am fine. Thanks a lot", "for asking me."]


class Src:  # 1 s per segment, each at its own level, so a clip shows which segments it holds
    def frames(self):
        yield from (np.full(SR, v, dtype=np.float32) for v in (0.1, 0.2, 0.3, 0.4))

    def close(self):
        pass


class OneSeg:
    def segments(self, frames):
        yield from frames


class SeqASR(Recognizer):
    def __init__(self):
        self.it = iter(TEXTS)

    def transcribe(self, audio):
        return Transcript(next(self.it), "en")


class Upper(Translator):
    def translate(self, text, src, tgt):
        return Translation(None, text.upper())


def _levels(path):
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, SR)
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2") / 32767.0
    return [round(float(pcm[i]), 1) for i in range(0, len(pcm), SR)]


def _session(tmp_path, monkeypatch, argv, sink=None):
    """cli.run_session with fake capture/ASR/MT and every socket call failing."""
    import tinglongwu.asr
    import tinglongwu.pipeline

    for obj, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "getaddrinfo")):
        monkeypatch.setattr(obj, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used")))
    real_run = tinglongwu.pipeline.run
    monkeypatch.setattr(tinglongwu.pipeline, "run", lambda *a, **k: real_run(
        Src(), OneSeg(), SeqASR(), Upper(), k.get("sink", a[4]), "zh", SentenceMerger(max_wait=60),
        audio_clock=True, recorder=k.get("recorder")))
    monkeypatch.setattr(tinglongwu.asr, "FasterWhisperRecognizer", lambda *a, **k: type("A", (), {"device": "cpu"})())
    monkeypatch.setattr(cli, "select_backend", lambda *a, **k: Src())
    monkeypatch.setattr(cli, "models_dir", lambda d=None: tmp_path)
    monkeypatch.setattr("tinglongwu.minspec.check_avx2", lambda *a, **k: None)
    monkeypatch.setenv("TLWU_UI_LANG", "en")
    monkeypatch.chdir(tmp_path)
    cli.run_session(cli.parse_args(["--transcript-only", "--no-adapt", "--model", "tiny", "--device", "cpu", *argv]), sink)


def test_record_default_off_writes_no_audio(tmp_path, monkeypatch, capsys):
    assert cli.parse_args([]).record is None and cli.parse_args(["--record", "d"]).record == "d"
    _session(tmp_path, monkeypatch, [])
    out = capsys.readouterr()
    assert not list(tmp_path.rglob("*.wav")) and not (tmp_path / "transcripts").exists()
    assert "consent" not in out.err and "● Recording" not in out.out


def test_record_one_wav_per_line_matching_jsonl_notice_and_mark(tmp_path, monkeypatch, capsys):
    _session(tmp_path, monkeypatch, ["--record"])
    [folder] = (tmp_path / "transcripts").glob("rec-*")
    rows = [json.loads(x) for x in (folder / "segments.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["source"] for r in rows] == ["Hello there my good friend how are you today.", "I am fine.",
                                          "Thanks a lot for asking me."]
    assert [r["translation"] for r in rows] == [r["source"].upper() for r in rows]
    assert sorted(p.name for p in folder.glob("*.wav")) == [r["file"] for r in rows] == ["0001.wav", "0002.wav", "0003.wav"]
    # a clip holds every segment its line has text of (segment 3 is split over lines 2 and 3)
    assert [_levels(folder / r["file"]) for r in rows] == [[0.1, 0.2], [0.3], [0.3, 0.4]]
    assert [(r["start"], r["end"], r["lang"]) for r in rows] == [(0.0, 2.0, "en"), (2.0, 3.0, "en"), (2.0, 4.0, "en")]
    out = capsys.readouterr()
    assert "consent" in out.err and str(folder.resolve()) in out.err
    assert out.out.count("● Recording") == 3 and all(x.startswith("● Recording ") for x in out.out.splitlines())


def test_record_marks_a_wrapped_console_sink(tmp_path, monkeypatch):  # meeting mode + save wraps the console sink
    _session(tmp_path, monkeypatch, ["--record"], sink=JsonlSaveSink(TextSink(buf := io.StringIO()), tmp_path / "t.jsonl"))
    assert buf.getvalue().count("● Recording") == 3

