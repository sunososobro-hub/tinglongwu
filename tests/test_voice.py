"""#330: Listen + voice (`tlwu --voice`): off by default, echo-safe output device, subtitles unchanged."""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from tinglongwu import cli, voice
from tinglongwu.sink import Result, Sink


class _Lines(Sink):
    def __init__(self):
        self.lines, self.sources = [], []

    def write(self, r):
        self.lines.append(r.target)

    def write_source(self, src, original):
        self.sources.append(original)


def _r(target):
    return Result("ru", "привет", None, target)


# --- option -------------------------------------------------------------------------------------------------------

def test_voice_is_off_by_default():
    a = cli.parse_args([])
    assert a.voice is False and a.voice_device is None


@pytest.mark.parametrize("argv", [["--voice", "--transcript-only"], ["--voice-device", "Headphones"],
                                  ["--voice-speaker", "2"], ["--voice-speaker", "0"]])
def test_voice_option_misuse_exits_2(argv):
    with pytest.raises(SystemExit) as e:
        cli.parse_args(argv)
    assert e.value.code == 2


def _session(monkeypatch, tmp_path, extra, opened, run=None):
    from tinglongwu import asr as asr_mod
    from tinglongwu import device as device_mod
    from tinglongwu import minspec
    from tinglongwu import pipeline as pipeline_mod

    class _A:
        device = "cpu"
        live = False

    class _T:
        def preload(self, *a):
            pass

    seen = {}

    def fake_open_voice(a, mdir, inner, say):
        opened.append(inner)
        return voice.VoiceSink(inner, lambda text: None), lambda: opened.append("closed")

    monkeypatch.setattr(minspec, "check_avx2", lambda force=False: None)
    monkeypatch.setattr(device_mod, "resolve_device", lambda compute, warn=None: "cpu")
    monkeypatch.setattr(asr_mod, "FasterWhisperRecognizer", lambda *a, **k: _A())
    monkeypatch.setattr(cli, "make_translator", lambda *a, **k: _T())
    monkeypatch.setattr(cli, "select_backend", lambda *a, **k: object())
    monkeypatch.setattr(pipeline_mod, "run", run or (lambda source, seg, asr, mt, sink, *a, **k: seen.update(sink=sink)))
    monkeypatch.setattr(voice, "open_voice", fake_open_voice)
    monkeypatch.setattr(cli, "announce_ready", lambda *a, **k: opened.append("ready"))
    cli.run_session(cli.parse_args(["--file", str(tmp_path / "in.wav"), "--model", "tiny", "--lang", "ru",
                                    "--models-dir", str(tmp_path)] + extra), sink=_Lines())
    return seen


def test_run_session_without_voice_never_opens_it(monkeypatch, tmp_path):
    opened = []
    seen = _session(monkeypatch, tmp_path, [], opened)
    assert opened == ["ready"] and isinstance(seen["sink"], _Lines)


def test_run_session_with_voice_wraps_the_subtitle_sink_and_closes_it(monkeypatch, tmp_path):
    opened = []
    seen = _session(monkeypatch, tmp_path, ["--voice"], opened)
    assert isinstance(seen["sink"], voice.VoiceSink) and isinstance(seen["sink"].inner, _Lines)
    assert opened[1:] == ["ready", "closed"]  # ready is announced after the voice loaded


def test_run_session_closes_the_voice_when_the_run_fails(monkeypatch, tmp_path):
    opened = []

    def boom(*a, **k):
        raise RuntimeError("audio device lost")

    with pytest.raises(RuntimeError):
        _session(monkeypatch, tmp_path, ["--voice"], opened, run=boom)
    assert opened[-1] == "closed"  # Kokoro worker and player are not leaked (GUI runs many sessions)


# --- echo guard ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("out, cap", [
    ("Speakers (Realtek(R) Audio)", "Speakers (Realtek(R) Audio)"),          # the captured speaker itself
    ("alsa_output.pci-0000_00_1f.3.analog-stereo", "Monitor of alsa_output.pci-0000_00_1f.3.analog-stereo"),
    ("CABLE Input (VB-Audio Virtual Cable)", "Headset Microphone (Jabra)"),  # virtual cable
    ("BlackHole 2ch", "BlackHole 2ch"),
    ("BlackHole 16ch", None),
    ("tlwu_sink", "Built-in Microphone"),
    ("Multi-Output Device", "BlackHole 2ch"),
    ("", "Speakers"),
])
def test_voice_output_rejected_when_it_would_be_recaptured(out, cap):
    assert voice.voice_output_problem(out, cap)


@pytest.mark.parametrize("out, cap", [
    ("Headphones (USB Audio)", "Speakers (Realtek(R) Audio)"),
    ("Headphones (USB Audio)", "Microphone Array (Realtek(R) Audio)"),
    ("MacBook Pro Speakers", None),  # file input: nothing is captured
])
def test_voice_output_accepted_on_a_separate_device(out, cap):
    assert voice.voice_output_problem(out, cap) is None


class _Player:
    def __init__(self, device, out_sr=48000, enabled=True):
        self.speaker_name, self.pushed, self.closed = device or "Speakers (Realtek(R) Audio)", [], False

    def push(self, audio, sr):
        self.pushed.append((audio, sr))

    def close(self):
        self.closed = True


class _Tts:
    made = []

    def __init__(self, py, model, engine, speaker, lang):
        self.args, self.closed = (py, model, engine, speaker, lang), False
        _Tts.made.append(self)

    def speak(self, text, lang=None):
        yield {"t": "chunk", "sr": 24000}, np.full(2400, 0.3, np.float32).tobytes()
        yield {"t": "done"}, None

    def close(self):
        self.closed = True


@pytest.fixture
def fakes(monkeypatch):
    from tinglongwu.reverse import proto, tts_client

    players = []

    def player(*a, **k):
        players.append(_Player(*a, **k))
        return players[-1]

    _Tts.made.clear()
    monkeypatch.setattr(proto, "Player", player)
    monkeypatch.setattr(tts_client, "TTSClient", _Tts)
    monkeypatch.setattr(voice, "capture_device_name", lambda file, mic, device: "Speakers (Realtek(R) Audio)")
    return players


def test_open_voice_refuses_the_captured_speaker(fakes, tmp_path):
    a = cli.parse_args(["--voice"])
    with pytest.raises(RuntimeError, match="being captured"):
        voice.open_voice(a, tmp_path, _Lines(), lambda m: None)
    assert fakes[0].closed and _Tts.made == []  # no worker started for a refused device


def test_open_voice_refuses_a_target_without_a_voice(fakes, tmp_path):
    with pytest.raises(RuntimeError, match="zh, en, ja"):
        voice.open_voice(cli.parse_args(["--voice", "--to", "ru", "--voice-device", "Headphones"]), tmp_path,
                         _Lines(), lambda m: None)


def test_open_voice_speaks_chinese_on_the_earphones(fakes, tmp_path):
    inner = _Lines()
    a = cli.parse_args(["--voice", "--voice-device", "Headphones (USB)", "--tts-python", "py", "--tts-model", "m"])
    sink, close = voice.open_voice(a, tmp_path, inner, lambda m: None)
    sink.write(_r("你好，今天的會議開始了。"))
    close()
    assert inner.lines == ["你好，今天的會議開始了。"]
    assert _Tts.made[0].args == ("py", "m", "kokoro", 0, "zh") and _Tts.made[0].closed
    audio = np.concatenate([x for x, _ in fakes[0].pushed])
    assert fakes[0].pushed and np.abs(audio).max() > 0.1  # non-silent audio reached the earphones


# --- subtitles unchanged ------------------------------------------------------------------------------------------

def test_voice_sink_keeps_subtitles_identical_and_never_waits_for_the_voice():
    plain, voiced, spoken = _Lines(), _Lines(), []
    gate, started = threading.Event(), threading.Event()

    def slow_speak(text):
        started.set()
        gate.wait(5)
        spoken.append(text)

    sink = voice.VoiceSink(voiced, slow_speak)
    lines = ["第一句。", "", "第二句。", "第三句。"]
    t0 = time.perf_counter()
    for i, t in enumerate(lines):
        plain.write(_r(t))
        sink.write(_r(t))
        if i == 0:
            assert started.wait(5)  # the first line is being spoken (blocked) while the rest arrive
    sink.write_source("ru", "привет")
    assert time.perf_counter() - t0 < 0.2  # the voice is still blocked: subtitles were not delayed
    assert voiced.lines == plain.lines and voiced.sources == ["привет"]
    gate.set()
    sink.close()
    assert spoken == ["第一句。", "第二句。", "第三句。"]  # in order, empty line not spoken


def test_voice_error_on_one_line_does_not_stop_the_next():
    spoken, errors = [], []

    def speak(text):
        if text == "bad":
            raise RuntimeError("worker died")
        spoken.append(text)

    sink = voice.VoiceSink(_Lines(), speak, on_error=errors.append)
    for t in ("bad", "好"):
        sink.write(_r(t))
    sink.close()
    assert spoken == ["好"] and len(errors) == 1


def test_kokoro_paths_default_to_talk_setup(tmp_path):
    py, model = voice.kokoro_paths(tmp_path)
    assert "tts-kokoro" in py and model == str(tmp_path / "kokoro")


def test_voice_skips_stale_lines_instead_of_falling_behind():
    """Translation faster than the voice: at most 2 lines wait; older waiting ones are skipped (subtitles keep all)."""
    started, gate, spoken, inner = threading.Event(), threading.Event(), [], _Lines()

    def speak(text):
        started.set()
        gate.wait(5)
        spoken.append(text)

    sink = voice.VoiceSink(inner, speak)
    sink.write(_r("一"))
    assert started.wait(5)  # "一" is being spoken
    for t in ("二", "三", "四", "五"):
        sink.write(_r(t))
    gate.set()
    sink.close()
    assert spoken == ["一", "四", "五"] and sink.dropped == 2
    assert inner.lines == ["一", "二", "三", "四", "五"]


def test_worker_error_with_no_audio_is_reported_not_silent(fakes, tmp_path):
    class _Broken(_Tts):
        def speak(self, text, lang=None):
            yield {"t": "error", "msg": "No module named 'misaki'"}, None

    from tinglongwu.reverse import tts_client

    tts_client.TTSClient = _Broken  # restored by the fixture's monkeypatch
    said = []
    a = cli.parse_args(["--voice", "--voice-device", "Headphones (USB)"])
    sink, close = voice.open_voice(a, tmp_path, _Lines(), said.append)
    sink.write(_r("你好。"))
    close()
    assert any("misaki" in m for m in said) and fakes[0].pushed == []


def test_close_keeps_the_voice_open_while_the_worker_is_still_speaking():
    gate = threading.Event()
    sink = voice.VoiceSink(_Lines(), lambda text: gate.wait(5))
    sink.write(_r("長句。"))
    assert sink.close(timeout=0.05) is False  # still speaking: caller must not close the TTS client/player
    gate.set()
    sink.t.join(5)


def test_speak_waits_for_playback_so_audio_does_not_pile_up_behind_the_backlog():
    """Synthesis faster than playback: speak() returns only once the player's queue is drained, so later lines wait
    in VoiceSink's 2-line backlog (where stale ones are skipped) instead of queuing as audio."""
    import queue as _queue

    class _SlowPlayer(_Player):
        def __init__(self):
            super().__init__("Headphones")
            self.q = _queue.Queue()
            threading.Thread(target=self._play, daemon=True).start()

        def push(self, audio, sr):
            self.q.put(audio)

        def _play(self):
            while True:
                self.q.get()
                time.sleep(0.1)  # one piece takes 0.1 s to play
                self.played = getattr(self, "played", 0) + 1

    player = _SlowPlayer()
    speak = voice.make_speaker(_Tts("py", "m", "kokoro", 0, "zh"), player)
    speak("你好。")
    assert player.q.empty()


def test_voice_wrapper_reports_whether_the_subtitle_sink_shows_source_lines():
    from tinglongwu.pipeline import sink_shows_source

    class _NoSource(Sink):
        def write(self, r):
            pass

    assert sink_shows_source(voice.VoiceSink(_Lines(), lambda t: None)) is True
    assert sink_shows_source(voice.VoiceSink(_NoSource(), lambda t: None)) is False  # no false t_source_shown


def test_stalled_playback_skips_lines_instead_of_queuing_more_audio():
    """The output device stops consuming audio: after the drain timeout the next line raises (reported via on_error)
    and pushes nothing, so the player's queue does not grow."""
    import queue as _queue

    class _StuckPlayer(_Player):
        def __init__(self):
            super().__init__("Headphones")
            self.q = _queue.Queue()

        def push(self, audio, sr):
            self.q.put(audio)

    player = _StuckPlayer()
    speak = voice.make_speaker(_Tts("py", "m", "kokoro", 0, "zh"), player, drain_timeout=0.05)
    with pytest.raises(RuntimeError, match="stalled"):
        speak("第一句。")  # pushed, then playback never drains: reported for this line too
    n = player.q.qsize()
    with pytest.raises(RuntimeError, match="stalled"):
        speak("第二句。")
    assert n > 0 and player.q.qsize() == n


def test_open_voice_close_keeps_player_and_tts_open_while_still_speaking(fakes, tmp_path, monkeypatch):
    gate, said = threading.Event(), []
    monkeypatch.setattr(voice, "make_speaker", lambda tts, player: lambda text: gate.wait(5))
    monkeypatch.setattr(voice, "CLOSE_TIMEOUT", 0.05)
    a = cli.parse_args(["--voice", "--voice-device", "Headphones (USB)"])
    sink, close = voice.open_voice(a, tmp_path, _Lines(), said.append)
    for t in ("一", "二"):
        sink.write(_r(t))
    close()
    assert not fakes[0].closed and not _Tts.made[0].closed  # worker still speaking: nothing closed under it
    assert any("left unspoken" in m for m in said)
    gate.set()


def test_unmatched_voice_device_is_a_one_line_error(monkeypatch, tmp_path):
    from tinglongwu.reverse import proto

    def no_match(device, out_sr=48000):
        raise LookupError(f"no output device matching {device!r}")

    monkeypatch.setattr(proto, "Player", no_match)
    with pytest.raises(RuntimeError, match="--voice-device"):
        voice.open_voice(cli.parse_args(["--voice", "--voice-device", "Nope"]), tmp_path, _Lines(), lambda m: None)
