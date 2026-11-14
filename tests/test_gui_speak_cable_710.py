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
    monkeypatch.setattr(voice, "capture_device_name", lambda file, mic, device: mic_name)
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
