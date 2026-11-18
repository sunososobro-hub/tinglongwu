"""#1544: video scene + saved Source "mic" must be visible, the mic must say when it hears nothing, and the log names the input."""
import sys
import types

import numpy as np

from tinglongwu.capture import mic as micmod
from tinglongwu.capture import _soundcard as scmod
from tinglongwu.gui.core import source_label_key, strip_silent_hint, toggled_source
from tinglongwu.i18n import MESSAGES, t


def test_video_scene_with_mic_source_shows_the_source():
    assert source_label_key({"scene": "video", "source": "mic"}) == "gui_src_mic"
    assert source_label_key({"scene": "video", "source": "system"}) == "gui_src_system"
    assert source_label_key({"scene": "video"}) == "gui_src_system"
    assert source_label_key({"scene": "meeting", "source": "mic"}) is None
    for lang in MESSAGES:
        assert "gui_src_mic" in MESSAGES[lang] and "listen_silent_mic" in MESSAGES[lang], lang


def test_toggle_switches_without_changing_input():
    s = {"scene": "video", "source": "mic"}
    assert toggled_source(s) == "system" and s["source"] == "mic"
    assert toggled_source({"source": "system"}) == "mic"


def test_mic_hint_is_stripped_in_every_language():
    for lang in MESSAGES:
        hint = MESSAGES[lang]["listen_silent_mic"].format(names="Mic A")
        assert strip_silent_hint("keep\n" + hint) == "keep"
    assert "Mic A" in t("listen_silent_mic", names="Mic A")


def test_mic_source_fires_on_silent_after_10s_of_zeros(monkeypatch, capsys):
    mic = types.SimpleNamespace(id="a", name="Test Mic")
    fake = types.SimpleNamespace(default_microphone=lambda: mic)
    monkeypatch.setitem(sys.modules, "soundcard", fake)
    state = {"n": 0}

    class CM:
        def __enter__(self):
            def rec(numframes):
                state["n"] += 1
                return np.zeros((numframes, 2), np.float32) if state["n"] <= 120 else np.full((numframes, 2), 0.3, np.float32)
            return types.SimpleNamespace(record=rec)

        def __exit__(self, *a):
            return False

    mic.recorder = lambda samplerate, channels: CM()
    monkeypatch.setattr(scmod, "patch_mixformat", lambda: None)
    monkeypatch.setattr(scmod, "suppress_capture_warnings", lambda: None)
    clock = {"t": 0.0}

    def tick():
        clock["t"] += 0.1
        return clock["t"]

    real = micmod.SilenceWatch
    monkeypatch.setattr(micmod, "SilenceWatch", lambda: real(clock=tick))
    src = micmod.MicSource()
    events = []
    src.on_silent = lambda names: events.append(("silent", names))
    src.on_sound = lambda: events.append(("sound",))
    gen = src.frames()
    for i, _ in enumerate(gen):
        if i >= 130:
            src._stop.set()
            break
    assert events and events[0] == ("silent", ["Test Mic"])
    assert events[-1] == ("sound",)
    assert "listen source = mic, device = Test Mic" in capsys.readouterr().err


def test_listen_silent_mic_says_stop_first():
    for lang in MESSAGES:
        assert MESSAGES[lang]["listen_silent_mic"]
    assert "Stop" in t("listen_silent_mic", names="x")
