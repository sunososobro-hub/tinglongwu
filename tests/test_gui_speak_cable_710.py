"""#710: the GUI "I speak" button speaks the translation into the meeting through VB-CABLE ("CABLE Input"), so the
Meet/Zoom microphone "CABLE Output" carries it; without VB-CABLE it says so in one line instead of failing silently."""
from __future__ import annotations

import numpy as np
import pytest

from tinglongwu import cli, voice
from tinglongwu.gui import core
from tinglongwu.i18n import MESSAGES as STRINGS, t
from tinglongwu.sink import Result, Sink

CABLE = "CABLE Input (VB-Audio Virtual Cable)"
WIN_OUTPUTS = ["Speakers (Realtek(R) Audio)", CABLE, "Headphones (USB Audio)"]


def _speak_argv(outputs, platform="win32", target="zh-tw", **saved):
    """What the GUI worker parses after one press of Speak (toggle("speak") + _work), in a fresh profile."""
    s = dict(core.DEFAULTS, target=target, **saved)
    run = core.quick_settings(s, "speak")
    hint = core.speak_route(run, core.meeting_cable(outputs, platform=platform), platform=platform)
    notes = []
    argv = core.settings_to_argv(run, outputs=core.voice_outputs(outputs) if run.get("voice") else None,
                                 notice=notes.append)
    return cli.parse_args(argv), hint, notes


def test_speak_with_vb_cable_speaks_the_translation_into_cable_input():
    a, hint, notes = _speak_argv(WIN_OUTPUTS)
    assert a.mic is True and a.lang == "zh" and a.to == "en"  # my Chinese speech -> English for the meeting
    assert a.voice is True and a.voice_device == CABLE  # voice played on CABLE Input
    assert notes == []  # the cable is not reported as "unplugged" although the earphone list hides cables
    assert hint == "gui_ready_speak_cable" and "CABLE Output" in t(hint)  # status line names the meeting mic


def test_speak_with_vb_cable_overrides_saved_earphone_voice():
    a, _, _ = _speak_argv(WIN_OUTPUTS, voice=True, voice_device="Headphones (USB Audio)")
    assert a.voice_device == CABLE


def test_english_speaker_gets_a_chinese_voice_into_the_cable():
    a, _, _ = _speak_argv(WIN_OUTPUTS, target="en")
    assert a.lang == "en" and a.to == "zh" and a.voice_device == CABLE and voice.voice_lang(a.to) == "zh"


def test_speak_without_vb_cable_shows_one_line_with_the_download_link():
    a, hint, notes = _speak_argv(["Speakers (Realtek(R) Audio)", "Headphones (USB Audio)"])
    assert a.mic is True and a.voice is False  # still runs: subtitles only, nothing played anywhere
    msg = t(hint)
    assert hint == "gui_speak_no_cable" and "vb-audio.com/Cable" in msg and "\n" not in msg


def test_speak_off_windows_is_unchanged():  # macOS BlackHole routing is out of scope (#687)
    a, hint, _ = _speak_argv(["BlackHole 16ch", "MacBook Pro Speakers"], platform="darwin")
    assert hint is None and a.voice is False


def test_listen_button_never_routes_into_the_cable():
    s = core.quick_settings(dict(core.DEFAULTS), "listen")
    a = cli.parse_args(core.settings_to_argv(s))
    assert a.voice is False and a.mic is False


@pytest.mark.parametrize("lang", sorted(STRINGS))
def test_new_strings_exist_in_every_ui_language(lang):
    for key in ("gui_ready_speak_cable", "gui_speak_no_cable"):
        assert STRINGS[lang][key] and "\n" not in STRINGS[lang][key]


# --- the CLI voice accepts the cable only when a real microphone is captured --------------------------------------

@pytest.mark.parametrize("cap", ["Microphone Array (Realtek(R) Audio)", "Headset Microphone (Jabra)"])
def test_cable_is_allowed_as_voice_output_when_capturing_a_real_microphone(cap):
    assert voice.voice_output_problem(CABLE, cap, mic=True) is None


@pytest.mark.parametrize("out, cap, mic", [
    (CABLE, "Microphone Array (Realtek(R) Audio)", False),  # listening to PC sound: the cable can be recorded
    (CABLE, "CABLE Output (VB-Audio Virtual Cable)", True),  # mic IS the cable's other end: feedback loop
    (CABLE, None, True),  # capture device unknown: cannot prove it is safe
    (CABLE, "Monitor of alsa_output.pci-0000_00_1f.3.analog-stereo", True),  # a loopback, not a microphone
])
def test_cable_is_still_refused_when_it_could_loop(out, cap, mic):
    assert voice.voice_output_problem(out, cap, mic=mic)


class _Player:
    def __init__(self, device, out_sr=48000, enabled=True):
        self.speaker_name, self.pushed, self.closed = device, [], False

    def push(self, audio, sr):
        self.pushed.append((audio, sr))

    def close(self):
        self.closed = True


class _Tts:
    def __init__(self, py, model, engine, speaker, lang):
        self.lang, self.closed = lang, False

    def speak(self, text, lang=None):
        yield {"t": "chunk", "sr": 24000}, np.full(2400, 0.3, np.float32).tobytes()
        yield {"t": "done"}, None

    def close(self):
        self.closed = True


class _Lines(Sink):
    def __init__(self):
        self.lines = []

    def write(self, r: Result) -> None:
        self.lines.append(r.target)


@pytest.mark.parametrize("mic_name, ok", [("Microphone Array (Realtek(R) Audio)", True),
                                          ("CABLE Output (VB-Audio Virtual Cable)", False)])
def test_open_voice_plays_english_into_cable_input_for_a_real_mic(monkeypatch, tmp_path, mic_name, ok):
    from tinglongwu.reverse import proto, tts_client

    players = []
    monkeypatch.setattr(proto, "Player", lambda *a, **k: players.append(_Player(*a, **k)) or players[-1])
    monkeypatch.setattr(tts_client, "TTSClient", _Tts)
    monkeypatch.setattr(voice, "resolve_capture", lambda file, mic, device: (mic_name, True))
    a, _, _ = _speak_argv(WIN_OUTPUTS)
    a.tts_python, a.tts_model = "py", "m"
    if not ok:
        with pytest.raises(RuntimeError, match="virtual cable"):
            voice.open_voice(a, tmp_path, _Lines(), lambda m: None)
        return
    said, inner = [], _Lines()
    sink, close = voice.open_voice(a, tmp_path, inner, said.append)
    sink.write(Result("zh", "大家好", None, "Hello everyone", tgt="en"))
    close()
    assert inner.lines == ["Hello everyone"] and players[0].speaker_name == CABLE
    assert players[0].pushed and np.abs(np.concatenate([x for x, _ in players[0].pushed])).max() > 0.1
    assert any(CABLE in m for m in said)


# --- review of PR #749 (AI FAIL 06:15Z, Opus CHANGES 07:01Z, CodeRabbit) ------------------------------------------

def test_speak_without_vb_cable_turns_a_saved_listen_voice_off():
    """Saved voice=True (earphones for Listen) + no cable: nothing may play on the old device while the notice says
    "subtitles only"."""
    a, hint, notes = _speak_argv(["Speakers (Realtek(R) Audio)", "Headphones (USB Audio)"],
                                 voice=True, voice_device="Headphones (USB Audio)")
    assert hint == core.SPEAK_HINT_NO_CABLE and a.mic is True
    assert a.voice is False and notes == []


@pytest.mark.parametrize("hint, want", [(core.SPEAK_HINT_NO_CABLE, (core.SPEAK_HINT_NO_CABLE, True)),
                                        (core.SPEAK_HINT_CABLE, ("", True)),
                                        (None, ("", False))])
def test_speak_start_view_opens_the_panel_for_both_speak_lines(hint, want):
    assert core.speak_start_view(hint) == want


class _FakeMic:
    def __init__(self, name):
        self.name = name


def _fake_soundcard(monkeypatch, mics, default=None):
    import sys
    import types

    sc = types.ModuleType("soundcard")
    sc.all_microphones = lambda include_loopback=False: [_FakeMic(n) for n in mics]
    sc.default_microphone = lambda: _FakeMic(default or mics[0])
    monkeypatch.setitem(sys.modules, "soundcard", sc)


MICS = ["Microphone Array (Realtek(R) Audio)", "CABLE Output (VB-Audio Virtual Cable)"]


def test_capture_substring_is_resolved_to_the_device_the_recorder_opens(monkeypatch):
    _fake_soundcard(monkeypatch, MICS)
    assert voice.resolve_capture(None, True, "Output") == ("CABLE Output (VB-Audio Virtual Cable)", True)
    assert voice.resolve_capture(None, True, None) == (MICS[0], True)
    assert voice.resolve_capture(None, True, "nope") == ("nope", False)  # unproven


def _open_speak_voice(monkeypatch, tmp_path, capture_device=None):
    from tinglongwu.reverse import proto, tts_client

    players = []
    monkeypatch.setattr(proto, "Player", lambda *a, **k: players.append(_Player(*a, **k)) or players[-1])
    monkeypatch.setattr(tts_client, "TTSClient", _Tts)
    a, _, _ = _speak_argv(WIN_OUTPUTS)
    a.tts_python, a.tts_model, a.device = "py", "m", capture_device
    offs, said, inner = [], [], _Lines()
    sink, close = voice.open_voice(a, tmp_path, inner, said.append, off=offs.append)
    return sink, close, players, offs, said, inner


@pytest.mark.parametrize("dev", ["Output", "nope"])
def test_cable_voice_is_refused_for_a_capture_name_that_is_the_cable_or_unproven(monkeypatch, tmp_path, dev):
    _fake_soundcard(monkeypatch, MICS)
    with pytest.raises(RuntimeError, match="virtual cable"):
        _open_speak_voice(monkeypatch, tmp_path, capture_device=dev)


def test_mic_reopened_on_the_cable_mutes_the_meeting_voice(monkeypatch, tmp_path):
    _fake_soundcard(monkeypatch, MICS)
    sink, close, players, offs, said, inner = _open_speak_voice(monkeypatch, tmp_path)
    assert sink.capture_guard is not None
    sink.capture_guard("Headset Microphone (Jabra)")  # another real mic: voice stays on
    assert offs == [] and sink.muted is None
    sink.capture_guard("CABLE Output (VB-Audio Virtual Cable)")  # device change fell back to the cable
    sink.capture_guard("CABLE Output (VB-Audio Virtual Cable)")
    assert len(offs) == 1 and offs[0].startswith("Voice off") and sink.muted
    sink.write(Result("zh", "大家好", None, "Hello everyone", tgt="en"))
    close()
    assert inner.lines == ["Hello everyone"] and players[0].pushed == []  # subtitles yes, voice no


def test_capture_reopen_reports_the_new_device_to_on_reopen(monkeypatch):
    import sys
    import types

    from tinglongwu.capture import _soundcard as scmod

    monkeypatch.setitem(sys.modules, "soundcard", types.ModuleType("soundcard"))
    monkeypatch.setattr(scmod.threading.Event, "wait", lambda self, t=None: self.is_set())
    lost = RuntimeError("Error 0x88890004")

    class Rec:
        def __init__(self, mic):
            self.mic = mic

        def __enter__(self):
            return self

        def record(self, numframes):
            if self.mic.fail:
                self.mic.fail = False
                raise lost
            return np.zeros((numframes, 2), dtype=np.float32)

        def __exit__(self, *exc):
            return False

    class Mic:
        def __init__(self, name, fail):
            self.name, self.fail = name, fail

        def recorder(self, samplerate, channels):
            return Rec(self)

    mics = [Mic(MICS[0], True), Mic(MICS[1], False)]

    class Src(scmod.SoundcardSource):
        def _pick_microphone(self, sc):
            return mics.pop(0)

    src, seen = Src(), []
    src.on_reopen = seen.append
    it = src._frames()
    next(it)
    src.close()
    assert seen == [MICS[1]]


def test_run_session_hands_the_cable_guard_to_the_capture_source(monkeypatch, tmp_path):
    """The Speak run (mic + voice on CABLE Input) connects sink.capture_guard to the mic source's on_reopen."""
    import io
    import sys

    from tinglongwu import asr as asr_mod
    from tinglongwu import device as device_mod
    from tinglongwu import minspec
    from tinglongwu import pipeline as pipeline_mod
    from tinglongwu.capture.mic import MicSource

    class Asr:
        device, live = "cpu", False

        def __init__(self, *a, **k):
            pass

    class Mt:
        def preload(self, src, tgt):
            pass

    src, guard, ran = MicSource(None), (lambda name: None), []

    class VSink(_Lines):
        capture_guard = staticmethod(guard)

    monkeypatch.setattr(minspec, "check_avx2", lambda force=False: None)
    monkeypatch.setattr(device_mod, "resolve_device", lambda compute, warn=None: "cpu")
    monkeypatch.setattr(asr_mod, "FasterWhisperRecognizer", Asr)
    monkeypatch.setattr(cli, "make_translator", lambda *a, **k: Mt())
    monkeypatch.setattr(cli, "select_backend", lambda *a, **k: src)
    monkeypatch.setattr(voice, "open_voice", lambda a, mdir, sink, say, off=None: (VSink(), lambda drop=False: None))
    monkeypatch.setattr(pipeline_mod, "run", lambda source, *a, **k: ran.append(source))
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    a, _, _ = _speak_argv(WIN_OUTPUTS)
    a.model, a.models_dir = "tiny", str(tmp_path)
    cli.run_session(a)
    assert ran == [src] and src.on_reopen is guard


# --- App.toggle("speak") on a real Tk window (needs a display; CI e2e: Xvfb) -------------------------------------

@pytest.fixture
def speak_app(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    import functools

    from tinglongwu import i18n
    from tinglongwu.gui import app

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(i18n, "_system_tag", lambda: "en-US")
    monkeypatch.setattr(i18n.userdirs, "write_config_value", lambda k, v: None)
    monkeypatch.setattr(i18n, "_override", "en")
    monkeypatch.setattr(app.App, "_offer_pack", lambda self, run, mode: False)
    monkeypatch.setattr(app, "speak_route", functools.partial(core.speak_route, platform="win32"))
    runs = []

    def fake_work(self, s, stop):
        runs.append(s)
        stop.wait(10)
        self.q.put(("stopped",))

    monkeypatch.setattr(app.App, "_work", fake_work)
    made = []

    def make(cable):
        monkeypatch.setattr(app, "meeting_cable", lambda: cable)
        a = app.App()
        a.settings["tour_seen"] = True
        a.root.update()
        made.append(a)
        return a, runs

    yield make
    for a in made:
        a.stop_ev.set()
        try:
            a.root.destroy()
        except tk.TclError:
            pass


def test_toggle_speak_without_cable_shows_the_notice_in_an_open_panel(speak_app):
    a, runs = speak_app(None)
    a.panel.withdraw()
    a.root.update()
    a.toggle("speak")
    a.root.update()
    assert a.panel.state() == "normal"  # the bar press opens the panel, so the line is seen
    assert "vb-audio.com/Cable" in a.v_notice.get()
    assert runs and runs[0]["voice"] is False


def test_toggle_speak_with_cable_shows_the_cable_output_ready_line(speak_app):
    from tinglongwu.cli import READY_STATUS

    a, runs = speak_app(CABLE)
    a.panel.withdraw()
    a.root.update()
    a.toggle("speak")
    a.q.put(("status", f"{READY_STATUS} in 1.0 s"))
    a.poll()
    a.root.update()
    assert a.panel.state() == "normal" and "CABLE Output" in a.v_status.get()
    assert a.v_notice.get() == "" and runs and runs[0]["voice_device"] == CABLE
