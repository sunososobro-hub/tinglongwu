"""#132 speak-mode latency: the back-translation must not delay TTS, sentences are sent one by one, and every
stage is timed. Fake MT/TTS/player; the real proto / confirm / logic code runs."""
import threading
import time
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
    monkeypatch.setattr("builtins.input", lambda *x: "y")
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
    assert set(it["lat"]) == {"mt", "back", "tts"} and all(v >= 0 for v in it["lat"].values())


def test_confirm_mode_debug_latency_reports_tts(monkeypatch, capsys):
    """#142: confirm mode (default) used to print tts=- and a total without synthesis time."""
    class SlowTTS(TTS):
        def speak(self, text):
            time.sleep(0.05)
            yield from super().speak(text)

    res, _ = _confirm(monkeypatch, MT(), SlowTTS(), debug_latency=True)
    err = capsys.readouterr().err
    lat = res["items"][0]["lat"]
    assert lat["tts"] >= 0.04
    line = [ln for ln in err.splitlines() if ln.startswith("lat ")]
    assert len(line) == 1 and "tts=-" not in line[0]
    assert f"total={lat['mt'] + lat['tts']:.2f}" in line[0]


def test_no_back_skips_the_second_mt_pass(monkeypatch, capsys):
    mt = MT()
    res, _ = _confirm(monkeypatch, mt, TTS(), no_back=True)
    assert mt.calls == ["en"] and res["items"][0]["back"] == "" and res["items"][0]["decision"] == "sent"
    assert "BACK" not in capsys.readouterr().out


def test_late_back_translation_is_printed_as_its_own_line():
    log, shown = [], threading.Event()
    keys = iter(["y"])

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
    lat_lines = [x for x in err.splitlines() if x.startswith("lat ")]
    assert lat_lines and lat_lines[0].startswith("lat asr=- mt=0.") and " back=- tts=0." in lat_lines[0]


def test_live_auto_prints_one_batch_style_latency_line_matching_report(capsys):
    import re
    clock = iter([0.0, 0.2, 0.2, 0.5, 0.5, 0.6, 0.9, 1.0, 1.0, 1.1, 1.2, 1.3])
    lat = Lat(clock=lambda: next(clock, 2.0))
    lat.set("asr", 0.3)
    res = {"items": []}
    proto._run_auto_live(iter([(1, "mic", "你好")]), MT(), TTS(), Player(), res, "en", lat)
    lines = [x for x in capsys.readouterr().err.splitlines() if x.startswith("latency ")]
    assert len(lines) == 1
    m = re.fullmatch(r"latency asr=([\d.]+)s mt=([\d.]+)s first_audio=([\d.]+)s done=([\d.]+)s", lines[0])
    assert m, lines[0]
    it = res["items"][0]
    assert [float(x) for x in m.groups()] == [it["t_asr"], it["t_mt"], it["t_first_audio"], it["t_tts_done"]]
    assert it["t_asr"] == 0.3 and it["t_asr"] <= it["t_mt"] <= it["t_first_audio"] <= it["t_tts_done"]


def test_live_auto_latency_line_without_audio_has_dash(capsys):
    class Mute:
        def speak(self, text):
            yield {"t": "done"}, None

    res = {"items": []}
    proto._run_auto_live(iter([(1, "mic", "你好")]), MT(), Mute(), Player(), res, "en")
    assert " first_audio=-s " in capsys.readouterr().err and res["items"][0]["t_first_audio"] is None


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


# ---- #139: confirm mode streams sentences, --mt hymt / auto = Hy-MT2, --pause (VAD tail) ----
def test_confirm_mode_sends_first_sentence_before_synthesizing_the_second(monkeypatch):
    log, pushed = [], threading.Event()

    class SlowTTS(TTS):  # sentence 2 takes until sentence 1 is on the output (or 2 s)
        def speak(self, text):
            if self.log:
                pushed.wait(2)
            yield from super().speak(text)

    class Out(Player):
        def push(self, audio, sr):
            super().push(audio, sr)
            pushed.set()

    monkeypatch.setattr("builtins.input", lambda *x: "y")
    res = {"items": []}
    proto._run_confirm(SimpleNamespace(to="en", no_back=True), iter([(1, "mic", "你好")]),
                       MT(en="First sentence. Second sentence."), SlowTTS(log=log), Out(log),
                       SimpleNamespace(enabled=False), res)
    # after "y" each sentence goes out as soon as it exists; before #139 the whole utterance was synthesized
    # first and sent as one concatenated push (tts, tts, push)
    assert [e[0] for e in log] == ["tts", "push", "tts", "push"]
    assert res["items"][0]["decision"] == "sent"


def test_confirm_session_streams_parts_and_reports_a_tts_error_after_partial_audio():
    sent, out, first_sent = [], [], threading.Event()

    def synth(ru):
        yield np.ones(3, np.float32), 24000
        assert first_sent.wait(2)  # part 1 is already on the output while part 2 is synthesized
        raise RuntimeError("tts died")

    def send(a, sr):
        sent.append(len(a))
        first_sent.set()

    s = ConfirmSession(synth, send, out=out.append, readline=lambda: "y")
    u = Utterance(1, "你好", "A. B.")
    s.submit(u)
    s.finish()
    s.run()
    assert sent == [3] and u.decision == "sent" and any("TTS error: tts died" in m for m in out)


def _hymt_file(tmp_path):
    from tinglongwu.hymt import hymt_path

    f = hymt_path(tmp_path)
    f.parent.mkdir(parents=True)
    f.write_bytes(b"gguf")
    return f


@pytest.mark.parametrize("kind,device,installed,free,want", [
    ("auto", "cuda", True, 4000, "hymt"),     # was qwen before #139
    ("auto", "cuda", False, 4000, "opus"),    # no Hy-MT2 GGUF: OPUS, even on a big GPU
    ("auto", "cuda", True, 1500, "opus"),     # below engines.HYMT_MIN_FREE_MIB
    ("auto", "cuda", True, None, "opus"),     # nvidia-smi unreadable
    ("auto", "cpu", True, 4000, "opus"),
    ("hymt", "cpu", True, None, "hymt"),
])
def test_choose_speak_mt(tmp_path, kind, device, installed, free, want):
    if installed:
        _hymt_file(tmp_path)
    assert proto.choose_speak_mt(kind, device, tmp_path, lambda: free)[0] == want


def test_build_translator_auto_on_cuda_uses_hymt_direct_for_both_directions(tmp_path):
    _hymt_file(tmp_path)

    class Hy:
        def __init__(self, *a):
            self.calls = []

        def preload(self, *a):
            pass

        def translate(self, text, src, tgt):
            self.calls.append((src, tgt))
            return SimpleNamespace(text=f"{tgt}:{text}")

    mt = proto.build_translator("auto", "cuda", tmp_path, None, "en", free_mib=lambda: 4000, hy_factory=Hy)
    assert mt.translate("你好", "zh", "en").text == "en:你好" and mt.translate("Hi", "en", "zh").text == "zh:Hi"
    assert mt.hy.calls == [("zh", "en"), ("en", "zh")] and mt.history == []


def test_build_translator_explicit_hymt_without_the_model_says_how_to_install(tmp_path):
    from tinglongwu.models import ModelNotFound

    with pytest.raises(ModelNotFound, match="--hymt"):
        proto.build_translator("hymt", "cuda", tmp_path, None, "en")


def _speech(sec):
    return np.full(int(sec * 16000), 0.5, np.float32)


def _silence(sec):
    return np.zeros(int(sec * 16000), np.float32)


def _nonzero_spans(buf):
    idx = np.flatnonzero(buf)
    if not len(idx):
        return []
    cuts = np.flatnonzero(np.diff(idx) > 1)
    starts, ends = np.r_[idx[0], idx[cuts + 1]], np.r_[idx[cuts], idx[-1]] + 1
    return list(zip(starts.tolist(), ends.tolist()))


def _frames(*parts):
    audio = np.concatenate(parts)
    return [audio[i:i + 1600] for i in range(0, len(audio), 1600)]  # 0.1 s frames


@pytest.mark.parametrize("pause,n_segments", [(0.3, 2), (0.8, 1)])
def test_pause_sets_the_silence_that_ends_a_sentence(pause, n_segments):
    seg, sil = proto.make_segmenter(6.0, "cpu", pause, vad_fn=_nonzero_spans, env={})
    out = list(seg.segments(_frames(_speech(1.0), _silence(0.55), _speech(1.0), _silence(1.5))))
    assert sil == pause and len(out) == n_segments  # a 0.55 s breath ends the sentence at --pause 0.3, not at 0.8


def test_pause_range_is_checked():
    a = SimpleNamespace(follow_langs="en", to="en", tts_engine="kokoro", speaker=None, no_tts=True, end_silence=0.1)
    assert "--pause" in proto.check_args(a)
    a.end_silence = 0.5
    assert proto.check_args(a) is None


def test_lat_line_reports_the_configured_vad_tail_in_the_total():
    lat = Lat(clock=iter([0.0, 0.25]).__next__, fixed={"vad": 0.5})
    lat.timed("mt", lambda: None)
    lat.set("tts", 0.25)
    t = lat.take()
    assert t == {"vad": 0.5, "mt": 0.25, "tts": 0.25}
    assert lat_line(t) == "lat vad=0.50 asr=- mt=0.25 back=- tts=0.25 total=1.00"
    assert lat.take() == {"vad": 0.5}  # the tail is reported with every utterance


def test_cli_accepts_hymt_and_pause(capsys):
    with pytest.raises(SystemExit) as e:
        proto.main(["--mt", "hymt", "--pause", "0.3", "--help"])
    out = capsys.readouterr().out
    assert e.value.code == 0 and "--pause" in out and "hymt" in out
    assert proto.build_parser().parse_args(["--pause", "0.3"]).end_silence == 0.3  # alias of --end-silence
