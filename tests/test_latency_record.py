"""#323: native per-line latency records (pipeline on_record, --paced, --latency-jsonl).

The pipeline test runs a real-time paced synthetic clip (0.5 s "speech", then silence) through the real Segmenter
and pipeline.run with stub ASR / MT of known durations, and checks every field against an independent stopwatch:
the time the source released the frame holding the last speech sample, and the time the sink got the line."""
from __future__ import annotations

import json
import time

import numpy as np
import pytest

from tinglongwu import cli
from tinglongwu.asr import Transcript
from tinglongwu.capture.base import SAMPLE_RATE, AudioSource
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.sink import Sink
from tinglongwu.stream import PacedSource
from tinglongwu.translate import Translation

FRAME = SAMPLE_RATE // 20  # 0.05 s
SPEECH_S, CLIP_S = 0.5, 1.4
ASR_S, MT_S = 0.15, 0.25
TOL = 0.10


class _Clip(AudioSource):
    """0.5 s of noise then silence; records the wall time each frame is handed on (after PacedSource's wait)."""

    def __init__(self, clip_s=CLIP_S):
        rng = np.random.default_rng(0)
        audio = np.zeros(int(clip_s * SAMPLE_RATE), np.float32)
        audio[: int(SPEECH_S * SAMPLE_RATE)] = rng.uniform(-0.2, 0.2, int(SPEECH_S * SAMPLE_RATE))
        self.audio, self.released = audio, []

    def frames(self):
        for i in range(0, len(self.audio), FRAME):
            yield self.audio[i:i + FRAME]

    def close(self):
        pass


class _Stamped(AudioSource):
    def __init__(self, inner, clip):
        self.inner, self.clip = inner, clip

    def frames(self):
        for f in self.inner.frames():
            self.clip.released.append(time.monotonic())
            yield f

    def close(self):
        self.inner.close()


def _vad(buf):  # energy VAD: spans of samples above 0.01 (no Silero model needed)
    idx = np.flatnonzero(np.abs(buf) > 0.01)
    return [(int(idx[0]), int(idx[-1]) + 1)] if len(idx) else []


class _Asr:
    def transcribe(self, seg):
        time.sleep(ASR_S)
        return Transcript("hello there.", "en", 0.0)


class _Mt:
    zh_variant = "tw"

    def translate(self, text, src, tgt):
        time.sleep(MT_S)
        return Translation(None, "你好。")

    def preload(self, *a):
        pass


class _Sink(Sink):
    def __init__(self):
        self.lines, self.src = [], []

    def write(self, r):
        self.lines.append((time.monotonic(), r))

    def write_source(self, lang, text):
        self.src.append(time.monotonic())


def test_record_fields_match_stopwatch():
    clip, sink, records = _Clip(), _Sink(), []
    seg = Segmenter(chunk=10.0, use_vad=True, step=0.25, vad_fn=_vad)
    run(_Stamped(PacedSource(clip), clip), seg, _Asr(), _Mt(), sink, "zh", merger=None, audio_clock=False,
        on_record=records.append)
    assert len(sink.lines) == 1 and len(records) == 1
    rec = records[0]
    t_eos = clip.released[int(SPEECH_S * SAMPLE_RATE) // FRAME - 1]  # frame whose end is the last speech sample
    t_shown = sink.lines[0][0]
    assert rec["total"] == pytest.approx(t_shown - t_eos, abs=TOL)
    assert rec["t_source_shown"] == pytest.approx(sink.src[0] - t_eos, abs=TOL)
    # the VAD needs 0.6 s of silence, checked every 0.25 s: the cut comes 0.75 s after the speech ended
    assert rec["vad"] == pytest.approx(0.75, abs=TOL)
    assert rec["asr"] == pytest.approx(ASR_S, abs=TOL)
    assert rec["merge"] == pytest.approx(0.0, abs=TOL)
    assert rec["mt"] == pytest.approx(MT_S, abs=TOL)
    assert rec["total"] == pytest.approx(rec["vad"] + rec["asr"] + rec["merge"] + rec["mt"], abs=0.005)
    assert rec["text"] == "hello there." and rec["lang"] == "en"
    assert sink.lines[0][1].target == "你好。"


def test_no_records_without_callback():
    clip, sink = _Clip(), _Sink()
    run(clip, Segmenter(chunk=10.0, use_vad=True, step=0.25, vad_fn=_vad), _Asr(), _Mt(), sink, "zh",
        audio_clock=True)
    assert len(sink.lines) == 1


def test_args_paced_needs_file_and_jsonl_needs_paced(tmp_path):
    with pytest.raises(SystemExit) as e:
        cli.parse_args(["--paced"])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        cli.parse_args(["--file", "x.wav", "--latency-jsonl", str(tmp_path / "l.jsonl")])
    assert e.value.code == 2
    a = cli.parse_args(["--file", "x.wav", "--paced", "--latency-jsonl", str(tmp_path / "l.jsonl")])
    assert a.paced and a.latency_jsonl


def _session(monkeypatch, tmp_path, extra):
    """cli.run_session with models stubbed; returns the kwargs and source pipeline.run got."""
    from tinglongwu import asr as asr_mod
    from tinglongwu import device as device_mod
    from tinglongwu import minspec
    from tinglongwu import pipeline as pipeline_mod

    class _A:
        device = "cpu"
        live = False

    seen = {}

    def fake_run(source, *a, **k):
        seen["source"], seen["kw"] = source, k
        if k.get("on_record"):
            k["on_record"]({"vad": 0.5, "asr": 0.1, "merge": 0.0, "mt": 0.2, "total": 0.8, "t_source_shown": None,
                            "lang": "ru", "text": "привет"})

    monkeypatch.setattr(minspec, "check_avx2", lambda force=False: None)
    monkeypatch.setattr(device_mod, "resolve_device", lambda compute, warn=None: "cpu")
    monkeypatch.setattr(asr_mod, "FasterWhisperRecognizer", lambda *a, **k: _A())
    monkeypatch.setattr(cli, "select_backend", lambda *a, **k: _Clip())
    monkeypatch.setattr(pipeline_mod, "run", fake_run)
    args = cli.parse_args(["--transcript-only", "--file", str(tmp_path / "in.wav"), "--model", "tiny",
                           "--models-dir", str(tmp_path)] + extra)
    cli.run_session(args)
    return seen


def test_run_session_paced_uses_live_clock_and_writes_jsonl(monkeypatch, tmp_path):
    out = tmp_path / "lat.jsonl"
    seen = _session(monkeypatch, tmp_path, ["--paced", "--latency-jsonl", str(out)])
    assert isinstance(seen["source"], PacedSource)
    assert seen["kw"]["audio_clock"] is False
    assert json.loads(out.read_text(encoding="utf-8").splitlines()[0])["total"] == 0.8


def test_run_session_unpaced_file_unchanged(monkeypatch, tmp_path):
    seen = _session(monkeypatch, tmp_path, [])
    assert not isinstance(seen["source"], PacedSource)
    assert seen["kw"]["audio_clock"] is True and seen["kw"]["on_record"] is None


class _WriteOnlySink(Sink):
    def __init__(self):
        self.lines = []

    def write(self, r):
        self.lines.append((time.monotonic(), r))


def test_eof_segment_keeps_its_trailing_silence():
    """0.4 s of silence at the end of the file (< the 0.6 s VAD pause): the end-of-input flush cuts the segment; vad
    is the time since the speech ended (0.4 s), not ~0."""
    clip, sink, records = _Clip(SPEECH_S + 0.4), _WriteOnlySink(), []
    run(_Stamped(PacedSource(clip), clip), Segmenter(chunk=10.0, use_vad=True, step=0.25, vad_fn=_vad), _Asr(),
        _Mt(), sink, "zh", merger=None, audio_clock=False, on_record=records.append)
    t_eos = clip.released[int(SPEECH_S * SAMPLE_RATE) // FRAME - 1]
    assert records[0]["vad"] == pytest.approx(0.4, abs=TOL)
    assert records[0]["total"] == pytest.approx(sink.lines[0][0] - t_eos, abs=TOL)


def test_sink_without_source_line_has_no_t_source_shown():
    clip, records = _Clip(), []
    run(clip, Segmenter(chunk=10.0, use_vad=True, step=0.25, vad_fn=_vad), _Asr(), _Mt(), _WriteOnlySink(), "zh",
        audio_clock=False, on_record=records.append)
    assert records[0]["t_source_shown"] is None


def test_merger_units_keep_their_own_segment_tag():
    """A buffered unit released by a later segment (language change, timeout) keeps the earlier segment's tag."""
    from tinglongwu.merge import SentenceMerger

    m = SentenceMerger(max_wait=5.0)
    assert m.add("so we went to the", "en", 0.0, tag="A") == []
    out = m.add("Привет всем.", "ru", 1.0, tag="B")  # language change: the English buffer is released first
    assert [(u.text, u.tag) for u in out] == [("so we went to the", "A"), ("Привет всем.", "B")]
    m.add("and then", "en", 2.0, tag="C")
    assert m.add("", "en", 3.0, tag="D") == []  # nothing new heard: buffer still C's
    assert [(u.text, u.tag) for u in m.due(10.0)] == [("and then", "C")]
    assert [(u.text, u.tag) for u in m.add("it rained. and", "en", 11.0, tag="E")] == [("it rained.", "E")]
    assert [u.tag for u in m.flush()] == ["E"]
