"""#1070: MicSource follows the OS default microphone mid-run (owner: change one place, tlwu follows)."""
import sys
import types

import numpy as np

from tinglongwu.capture import _soundcard as scmod
from tinglongwu.capture.mic import MicSource


class Mic:
    def __init__(self, id_, name):
        self.id, self.name = id_, name

    def recorder(self, samplerate, channels):
        mic = self

        class CM:
            def __enter__(self):
                return types.SimpleNamespace(record=lambda numframes: (np.full((numframes, 2), 0.1 if mic.id == "b" else 0.0)))

            def __exit__(self, *a):
                return False
        return CM()


def run(defaults, monkeypatch, blocks=6):
    state = {"i": 0}
    fake = types.SimpleNamespace(default_microphone=lambda: defaults[min(state["i"], len(defaults) - 1)])
    monkeypatch.setitem(sys.modules, "soundcard", fake)
    monkeypatch.setattr(scmod, "FOLLOW_S", 0.0)
    src = MicSource(None)
    seen = []
    src.on_reopen = seen.append
    peaks = []
    for f in src._frames():
        peaks.append(float(np.abs(f).max()))
        state["i"] += 1
        if len(peaks) >= blocks:
            src.close()
    return peaks, seen


def test_switches_to_new_default(monkeypatch):
    a, b = Mic("a", "Headset Mic"), Mic("b", "USB Mic")
    peaks, seen = run([a, a, b], monkeypatch)
    assert seen == ["USB Mic"]
    assert peaks[0] == 0.0 and peaks[-1] > 0.0


def test_never_follows_onto_cable(monkeypatch):
    a, c = Mic("a", "Headset Mic"), Mic("c", "CABLE Output (VB-Audio Virtual Cable)")
    peaks, seen = run([a, a, c], monkeypatch)
    assert seen == [] and max(peaks) == 0.0


def test_named_device_does_not_follow(monkeypatch):
    a, b = Mic("a", "Headset Mic"), Mic("b", "USB Mic")
    defaults = [a, b]
    state = {"i": 0}
    fake = types.SimpleNamespace(default_microphone=lambda: defaults[min(state["i"], 1)])
    monkeypatch.setitem(sys.modules, "soundcard", fake)
    monkeypatch.setattr(scmod, "FOLLOW_S", 0.0)
    src = MicSource("Headset")
    monkeypatch.setattr(src, "_pick_microphone", lambda sc: a)  # the named device stays picked
    seen, peaks = [], []
    src.on_reopen = seen.append
    for f in src._frames():
        peaks.append(float(np.abs(f).max()))
        state["i"] += 1
        if len(peaks) >= 6:
            src.close()
    assert seen == [] and max(peaks) == 0.0  # default moved to USB Mic, the named Headset is kept
