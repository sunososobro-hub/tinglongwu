"""0.3.8: the "10 s without the other side's sound" hint is cleared when sound or an other-side bubble arrives."""
import numpy as np

from tinglongwu.capture import wasapi
from tinglongwu.gui.core import strip_silent_hint
from tinglongwu.i18n import MESSAGES, t


def test_strip_removes_hint_keeps_other_lines():
    for lang in MESSAGES:
        hint = t("listen_silent", names="A / B") if lang == "zh-TW" else MESSAGES[lang]["listen_silent"].format(names="A")
        assert strip_silent_hint(hint) == ""
        assert strip_silent_hint("keep me\n" + hint) == "keep me"
    assert strip_silent_hint("only other") == "only other"


def test_on_sound_fires_once_after_silence(monkeypatch):
    src = wasapi.WasapiLoopbackSource()
    src.listening = ["x"]
    events = []
    src.on_silent = lambda n: events.append("silent")
    src.on_sound = lambda: events.append("sound")
    quiet, loud = np.zeros((10, 2), np.float32), np.full((10, 2), 0.5, np.float32)
    clock = iter([0, 11, 12, 13, 14, 15])
    real = wasapi.SilenceWatch
    monkeypatch.setattr(wasapi, "SilenceWatch", lambda: real(clock=lambda: next(clock)))
    monkeypatch.setattr(wasapi.SoundcardSource, "frames", lambda self: iter([quiet, quiet, loud, loud]))
    src.device = "x"
    list(src.frames())
    assert events == ["silent", "sound"]
