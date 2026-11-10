"""#132 speak-mode latency: the back-translation must not delay TTS, sentences are sent one by one, and every
stage is timed. Fake MT/TTS/player; the real proto / confirm / logic code runs."""
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from tinglongwu.reverse import proto
from tinglongwu.reverse.confirm import ConfirmSession, Utterance
from tinglongwu.reverse.logic import Lat, TimedASR, lat_line


class MT:
    """zh->en answers at once; the back-translation (->zh) records whether synthesis had already started."""

    def __init__(self, tts_started=None, en="Hello there."):
        self.history, self._pair, self.calls, self.en = [], None, [], en
        self.tts_started, self.tts_was_running = tts_started, None

    def translate(self, text, src, tgt):
        self.calls.append(tgt)
        if tgt == "zh":
            if self.tts_started is not None:
                self.tts_was_running = self.tts_started.wait(2)
            return SimpleNamespace(text="你好回譯")
        return SimpleNamespace(text=self.en)


class TTS:
    engine = "none"

    def __init__(self, started=None, log=None):
        self.started, self.log = started, log if log is not None else []

    def speak(self, text):
        self.log.append(("tts", text))
        if self.started is not None:
            self.started.set()
        yield {"t": "chunk", "sr": 24000}, np.ones(4, np.float32).tobytes()
        yield {"t": "done"}, None


class Player:
    enabled = True

    def __init__(self, log=None):
        self.log = log if log is not None else []

    def push(self, audio, sr):
        self.log.append(("push", len(audio)))


def _confirm(monkeypatch, mt, tts, **flags):
    monkeypatch.setattr("builtins.input", lambda *x: "")
    res, player = {"items": []}, Player()
    proto._run_confirm(SimpleNamespace(to="en", **flags), iter([(1, "mic", "你好")]), mt, tts, player,
                       SimpleNamespace(enabled=False), res)
    return res, player


def test_confirm_mode_synthesis_starts_before_back_translation(monkeypatch, capsys):
    started = threading.Event()
    mt = MT(started)
    res, player = _confirm(monkeypatch, mt, TTS(started))
    assert mt.tts_was_running is True  # before the fix TTS was only submitted after the back-translation
    it = res["items"][0]
    assert it["decision"] == "sent" and it["back"] == "你好回譯" and player.log == [("push", 4)]
    assert "← BACK(zh): 你好回譯" in capsys.readouterr().out
    assert set(it["lat"]) == {"mt", "back"} and all(v >= 0 for v in it["lat"].values())


def test_no_back_skips_the_second_mt_pass(monkeypatch, capsys):
    mt = MT()
    res, _ = _confirm(monkeypatch, mt, TTS(), no_back=True)
    assert mt.calls == ["en"] and res["items"][0]["back"] == "" and res["items"][0]["decision"] == "sent"
    assert "BACK" not in capsys.readouterr().out


def test_late_back_translation_is_printed_as_its_own_line():
    log, shown = [], threading.Event()
    keys = iter([""])

    def readline():
        shown.set()
        gate.wait(2)
        return next(keys)

    gate = threading.Event()
    s = ConfirmSession(lambda ru: (np.ones(2, np.float32), 24000), lambda a, sr: None, out=log.append,
                       readline=readline)
    u = Utterance(1, "你好", "Hello")
    s.submit(u)
    s.finish()
    th = threading.Thread(target=s.run)
    th.start()
    assert shown.wait(2) and not any("BACK" in m for m in log)  # shown without waiting for the back-translation
    s.set_back(u, "你好")
    gate.set()
    th.join(5)
    assert any(m.strip() == "← BACK(zh): 你好" for m in log) and u.decision == "sent" and u.back == "你好"


def test_auto_mode_sends_first_sentence_before_synthesizing_the_second(capsys):
    log = []
    res = {"items": []}
    proto._run_auto_live(iter([(1, "mic", "你好")]), MT(en="First sentence. Second sentence."), TTS(log=log),
                         Player(log), res, "en", show_lat=True)
    assert [e[0] for e in log] == ["tts", "push", "tts", "push"]  # was tts, tts, push (one concatenated push)
    it = res["items"][0]
    assert it["decision"] == "sent" and set(it["lat"]) == {"mt", "tts"}
    err = capsys.readouterr().err
    assert err.startswith("lat asr=- mt=0.") and " back=- tts=0." in err and "total=" in err


def test_auto_mode_without_audio_is_reported():
    class Mute:
        def speak(self, text):
            yield {"t": "done"}, None

    res = {"items": []}
    proto._run_auto_live(iter([(1, "mic", "你好")]), MT(), Mute(), Player(), res, "en")
    assert res["items"][0]["decision"] == "no_audio" and "tts" not in res["items"][0]["lat"]


def test_lat_collects_stage_times_and_formats_one_line():
    ticks = iter([0.0, 0.4, 1.0, 1.25, 2.0, 2.5, 3.0, 3.5])
    lat = Lat(clock=lambda: next(ticks))

    class Asr:
        _last = "x"

        def transcribe(self, seg):
            return "tr"

    asr = TimedASR(Asr(), lat)
    assert asr.transcribe(b"") == "tr" and asr._last == "x"
    assert lat.timed("mt", lambda: 7) == 7
    lat.set("tts", 0.35)
    t = lat.take()
    assert t == {"asr": 0.4, "mt": 0.25, "tts": 0.35} and lat.t == {}
    assert lat_line(t) == "lat asr=0.40 mt=0.25 back=- tts=0.35 total=1.00"
    assert lat_line({"mt": 0.2, "back": 0.5}) == "lat asr=- mt=0.20 back=0.50 tts=- total=0.20"
    asr.transcribe(b"")  # a dropped segment ...
    asr.transcribe(b"")  # ... does not add up with the next one
    assert lat.take() == {"asr": 0.5}


def test_cli_documents_the_new_flags(capsys):
    with pytest.raises(SystemExit) as e:
        proto.main(["--help"])
    out = capsys.readouterr().out
    assert e.value.code == 0 and "--no-back" in out and "--debug-latency" in out
