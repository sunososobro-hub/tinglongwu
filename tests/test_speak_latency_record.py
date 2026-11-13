"""#434: speak mode --input X --paced replays a file in real time through the live VAD segmenter, and
--latency-jsonl writes one record per spoken line (vad / asr / merge / mt / total / tts_first_audio /
total_first_audio).

The main test writes a synthetic clip (0.5 s of "speech", then silence) to a wav, runs it through the product path
(_live_main -> paced_file_source -> real Segmenter -> capture_segments -> recognize_utterances -> _run_auto_live)
with stub ASR / MT / TTS that sleep known times, and checks every field against an independent stopwatch: the wall
time the speech ended (first frame release + 0.5 s) and the times the stubs ran / the player got the first chunk."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from tinglongwu.asr import Transcript
from tinglongwu.capture import file as file_mod
from tinglongwu.capture.base import SAMPLE_RATE
from tinglongwu.reverse import proto
from tinglongwu.segment import Segmenter

SPEECH_S, CLIP_S = 0.5, 1.6
ASR_S, MT_S, TTS_S = 0.15, 0.25, 0.3
TOL = 0.10


def _vad(buf):  # energy VAD: spans of samples above 0.01 (no Silero model needed)
    idx = np.flatnonzero(np.abs(buf) > 0.01)
    return [(int(idx[0]), int(idx[-1]) + 1)] if len(idx) else []


@pytest.fixture
def clip(tmp_path):
    rng = np.random.default_rng(0)
    audio = np.zeros(int(CLIP_S * SAMPLE_RATE), np.float32)
    audio[: int(SPEECH_S * SAMPLE_RATE)] = rng.uniform(-0.3, 0.3, int(SPEECH_S * SAMPLE_RATE))
    path = tmp_path / "clip.wav"
    sf.write(str(path), audio, SAMPLE_RATE)
    return path


class _Watch:
    """The independent stopwatch (time.perf_counter, the clock Lat uses in production)."""

    def __init__(self):
        self.t = {}

    def mark(self, k):
        self.t.setdefault(k, time.perf_counter())


def _run(monkeypatch, clip, watch, tts=True):
    orig_frames = file_mod.FileSource.frames

    def frames(self):  # stopwatch: paced playback starts when the first frame is asked for
        for i, f in enumerate(orig_frames(self)):
            if i == 0:
                watch.mark("start")
            yield f

    monkeypatch.setattr(file_mod.FileSource, "frames", frames)
    # the product segmenter settings (0.5 s silence, 0.25 s step) with an energy VAD instead of the Silero model
    monkeypatch.setattr(proto, "make_segmenter",
                        lambda chunk, dev="cpu", pause=None, **k: (Segmenter(chunk, True, min_silence=0.5, step=0.25,
                                                                             vad_fn=_vad), 0.5))

    class Asr:
        def transcribe(self, seg):
            watch.mark("asr_start")
            time.sleep(ASR_S)
            watch.mark("asr_end")
            return Transcript("你好朋友", "zh", 0.0)

    class Mt:
        def translate(self, text, src, tgt):
            watch.mark("mt_start")
            time.sleep(MT_S)
            watch.mark("mt_end")
            return SimpleNamespace(text="Hello friend.")

    class Tts:
        engine = "none"

        def speak(self, text):
            time.sleep(TTS_S)
            yield {"t": "chunk", "sr": 24000}, np.ones(2400, np.float32).tobytes()
            yield {"t": "done"}, None

    class Player:
        enabled = True

        def push(self, audio, sr):
            watch.mark("push")

        def close(self):
            pass

    recs = []
    a = SimpleNamespace(paced=True, input=str(clip), chunk_s=6.0, dev="cpu", end_silence=None, to="en",
                        on_record=recs.append)
    res = {"items": []}
    proto._live_main(a, Asr(), Mt(), Tts() if tts else None, Player(), SimpleNamespace(enabled=False), False, res)
    return recs, res


def test_paced_speak_record_matches_stopwatch(monkeypatch, clip):
    watch = _Watch()
    recs, res = _run(monkeypatch, clip, watch)
    assert len(recs) == 1 and len(res["items"]) == 1
    r, w = recs[0], watch.t
    eos = w["start"] + SPEECH_S  # the frame holding the last speech sample is released 0.5 s after playback start
    want = {"vad": w["asr_start"] - eos, "asr": w["asr_end"] - w["asr_start"], "merge": w["mt_start"] - w["asr_end"],
            "mt": w["mt_end"] - w["mt_start"], "total": w["mt_end"] - eos,
            "tts_first_audio": w["push"] - w["mt_end"], "total_first_audio": w["push"] - eos}
    for k, v in want.items():
        assert r[k] == pytest.approx(v, abs=TOL), (k, r, want)
    # the real VAD wait is in the record: >= the 0.5 s configured silence (rounded up to the 0.25 s step)
    assert r["vad"] >= 0.5 - TOL
    assert r["asr"] == pytest.approx(ASR_S, abs=TOL) and r["mt"] == pytest.approx(MT_S, abs=TOL)
    assert r["tts_first_audio"] == pytest.approx(TTS_S, abs=TOL)
    assert r["total"] == pytest.approx(r["vad"] + r["asr"] + r["merge"] + r["mt"], abs=0.003)
    assert r["total_first_audio"] == pytest.approx(r["total"] + r["tts_first_audio"], abs=0.003)
    assert all(round(r[k], 3) == r[k] for k in want)
    assert r["lang"] == "en" and r["text"] == "Hello friend."
    # real time: the record can only exist after the clip was (nearly) played
    assert time.perf_counter() - w["start"] >= SPEECH_S + 0.5


def test_paced_text_only_record_has_no_tts_fields(monkeypatch, clip):
    recs, _ = _run(monkeypatch, clip, _Watch(), tts=False)
    assert len(recs) == 1
    r = recs[0]
    assert r["tts_first_audio"] is None and r["total_first_audio"] is None
    assert r["total"] == pytest.approx(r["vad"] + r["asr"] + r["merge"] + r["mt"], abs=0.003)


def test_paced_without_input_exits_2(capsys):
    with pytest.raises(SystemExit) as e:
        proto.main(["--paced", "--no-tts"])
    assert e.value.code == 2 and "--paced needs --input" in capsys.readouterr().err


def test_latency_jsonl_needs_paced_or_live_mic(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        proto.main(["--input", str(tmp_path), "--latency-jsonl", str(tmp_path / "s.jsonl"), "--no-tts"])
    assert e.value.code == 2 and "--latency-jsonl needs" in capsys.readouterr().err


def test_latency_recorder_appends_one_json_line_per_record(tmp_path):
    from tinglongwu.cli import latency_recorder
    from tinglongwu.reverse.logic import speak_record

    path = tmp_path / "s.jsonl"
    on_record, fh = latency_recorder(str(path), None)
    marks = {"eos": 10.0, "cut": 10.6, "asr_done": 10.8}
    on_record(speak_record(marks, 10.8, 11.1, 11.45, lang="ru", text="x"))
    on_record(speak_record(marks, 10.8, 11.1, None, lang="ru", text="y"))
    fh.close()
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert lines[0] == {"vad": 0.6, "asr": 0.2, "merge": 0.0, "mt": 0.3, "total": 1.1, "tts_first_audio": 0.35,
                        "total_first_audio": 1.45, "lang": "ru", "text": "x"}
    assert lines[1]["tts_first_audio"] is None and lines[1]["total_first_audio"] is None
