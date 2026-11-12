"""#317: capture survives WASAPI device invalidation (0x88890004): close, re-pick, keep yielding; give up cleanly."""
import numpy as np
import pytest

from tinglongwu.capture import _soundcard as scmod
from tinglongwu.capture._soundcard import AudioDeviceLost, SoundcardSource

LOST = RuntimeError("Error 0x88890004")


class FakeRecorder:
    def __init__(self, mic):
        self.mic = mic

    def __enter__(self):
        err = self.mic.open_errors.pop(0) if self.mic.open_errors else None
        if err is not None:
            raise err
        return self

    def record(self, numframes):
        step = self.mic.script.pop(0) if self.mic.script else "ok"
        if isinstance(step, BaseException):
            raise step
        return np.zeros((numframes, 2), dtype=np.float32)

    def __exit__(self, *exc):
        self.mic.closed += 1
        if exc[0] is not None and "0x8889" in str(exc[1]):  # like soundcard on an invalidated device
            raise RuntimeError("Error 0x100000001")
        return False


class FakeMic:
    def __init__(self, name, script=(), open_errors=()):
        self.name, self.script, self.open_errors, self.closed = name, list(script), list(open_errors), 0

    def recorder(self, samplerate, channels):
        return FakeRecorder(self)


class Src(SoundcardSource):
    def __init__(self, mics, device=None):
        super().__init__(device)
        self.mics, self.picks = list(mics), []

    def _pick_microphone(self, sc):
        self.picks.append(self.device)
        if self.device == "gone":
            raise RuntimeError("No capture device matching 'gone'")
        return self.mics.pop(0) if len(self.mics) > 1 else self.mics[0]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    import sys
    import types
    monkeypatch.setitem(sys.modules, "soundcard", types.ModuleType("soundcard"))  # no audio server needed
    waits = []
    monkeypatch.setattr(scmod.threading.Event, "wait", lambda self, t=None: waits.append(t) or self.is_set())
    return waits


def take(src, n):
    out, it = [], src._frames()
    for _ in range(n):
        out.append(next(it))
    src.close()
    return out


def test_invalidated_device_is_reopened_and_frames_continue(capsys, no_sleep):
    first = FakeMic("Headset", script=["ok", LOST])
    second = FakeMic("Speakers")
    src = Src([first, second])
    blocks = take(src, 4)
    assert len(blocks) == 4 and all(b.dtype == np.float32 and len(b) == 1600 for b in blocks)
    assert first.closed == 1 and len(src.picks) == 2  # recorder closed, device re-picked
    err = capsys.readouterr().err
    assert "audio device changed, reopened Speakers" in err
    assert "Error 0x100000001" not in err and "Traceback" not in err
    assert no_sleep == [0.5]


def test_retry_limit_gives_clean_error_with_backoff(capsys, no_sleep):
    mic = FakeMic("Headset", script=[LOST], open_errors=[None] + [LOST] * 10)
    src = Src([mic])
    with pytest.raises(AudioDeviceLost) as ei:
        list(src._frames())
    assert "could not be reopened after 5 tries" in str(ei.value)
    assert "0x88890004" in str(ei.value) and "\n" not in str(ei.value)
    assert no_sleep == [0.5, 1.0, 2.0, 4.0, 5.0]


def test_counter_resets_after_a_good_block(no_sleep):
    # 4 consecutive failures, a good block, 4 more: never reaches the limit of 5, backoff restarts at 0.5 s
    mic = FakeMic("Headset", script=["ok", LOST, "ok", LOST, "ok"],
                  open_errors=[None, LOST, LOST, LOST, None, LOST, LOST, LOST, None])
    src = Src([mic])
    blocks = take(src, 3)
    assert len(blocks) == 3
    assert no_sleep == [0.5, 1.0, 2.0, 4.0, 0.5, 1.0, 2.0, 4.0]


def test_named_device_gone_falls_back_to_default(capsys):
    named = FakeMic("USB Mic", script=[LOST])
    default = FakeMic("Default Mic")

    class Named(Src):
        def _pick_microphone(self, sc):
            self.picks.append(self.device)
            if self.device and len(self.picks) > 1:
                raise RuntimeError("No capture device matching 'usb mic'")
            return named if self.device else default

    src = Named([named], device="usb mic")
    take(src, 1)
    assert src.picks == ["usb mic", "usb mic", None] and src.device == "usb mic"
    assert "reopened Default Mic" in capsys.readouterr().err


def test_other_errors_are_not_retried():
    mic = FakeMic("Headset", script=[ValueError("boom")])
    with pytest.raises(ValueError):
        list(Src([mic])._frames())


def test_backoff_schedule():
    assert [scmod.backoff(n) for n in range(1, 7)] == [0.5, 1.0, 2.0, 4.0, 5.0, 5.0]
