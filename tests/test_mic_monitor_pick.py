from types import SimpleNamespace

import pytest

from tinglongwu.capture.mic import MicSource
from tinglongwu.reverse.live import check_mic_feedback

REAL = SimpleNamespace(name="Family 17h/19h HD Audio Controller Analog Stereo")
MON = SimpleNamespace(name="Monitor of Family 17h/19h HD Audio Controller Analog Stereo")


def _sc(calls):
    def all_microphones(include_loopback=False):
        calls.append(include_loopback)
        return [REAL, MON] if include_loopback else [REAL]
    return SimpleNamespace(all_microphones=all_microphones)


def test_monitor_name_searches_loopback():
    calls = []
    assert MicSource("Monitor of Family")._pick_microphone(_sc(calls)) is MON
    assert calls[0] is True


def test_other_name_unchanged():
    calls = []
    assert MicSource("Family 17h")._pick_microphone(_sc(calls)) is REAL
    assert calls == [False]
    with pytest.raises(RuntimeError):
        MicSource("Monitor of Nope")._pick_microphone(_sc([]))


def test_tlwu_monitor_still_rejected():
    with pytest.raises(ValueError):
        check_mic_feedback("Monitor of tlwu_sink", None, allow_monitor=True)
