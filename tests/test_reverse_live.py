import threading
from types import SimpleNamespace

import numpy as np
import pytest

from tinglongwu.reverse import proto
from tinglongwu.reverse.live import (capture_segments, check_mic_feedback, recognize_utterances,
                                     resolve_mic_name)

NAMES = ["Microphone (Realtek)", "CABLE Output (VB-Audio Virtual Cable)", "Headset Mic"]


def test_resolve_mic_substring_and_default():
    assert resolve_mic_name("headset", NAMES, "x") == "Headset Mic"
    assert resolve_mic_name(None, NAMES, "Microphone (Realtek)") == "Microphone (Realtek)"
    with pytest.raises(LookupError):
        resolve_mic_name("nope", NAMES, "x")
    with pytest.raises(LookupError):
        resolve_mic_name(None, NAMES, None)


@pytest.mark.parametrize("mic,out,spk", [
    ("CABLE Output (VB-Audio Virtual Cable)", "CABLE Input", None),
    ("CABLE Output (VB-Audio Virtual Cable)", None, None),  # cable output is always refused
    ("Speakers (Realtek)", "Speakers", None),
    ("CABLE Output (VB)", "CABLE Input", "CABLE Input (VB-Audio Virtual Cable)"),
])
def test_feedback_guard_refuses(mic, out, spk):
    with pytest.raises(ValueError, match="feedback guard"):
        check_mic_feedback(mic, out, spk)


def test_feedback_guard_linux_same_card_name_ok():
    name = "Family 17h/19h HD Audio Controller Analog Stereo"
    check_mic_feedback(name, "Family", name, same_name_ok=True)
    with pytest.raises(ValueError):
        check_mic_feedback(name, "Family", name)
    with pytest.raises(ValueError):
        check_mic_feedback("tlwu_mic", "tlwu_sink", None, same_name_ok=True)
    with pytest.raises(ValueError):
        check_mic_feedback("Monitor of " + name, "Family", None, same_name_ok=True)


def test_feedback_guard_allows_real_mic():
    check_mic_feedback("Microphone (Realtek)", "CABLE Input", "CABLE Input (VB-Audio Virtual Cable)")
    check_mic_feedback("Headset Mic", None, None)


def test_main_refuses_cable_output_mic(monkeypatch, capsys):
    sc = SimpleNamespace(
        default_microphone=lambda: SimpleNamespace(name="Headset Mic"),
        all_microphones=lambda include_loopback=False: [SimpleNamespace(name=n) for n in NAMES],
        all_speakers=lambda: [SimpleNamespace(name="CABLE Input (VB-Audio Virtual Cable)")])
    monkeypatch.setitem(__import__("sys").modules, "soundcard", sc)
    rc = proto.main(["--mic", "CABLE Output", "--device", "CABLE Input"])
    assert rc == 2 and "feedback guard" in capsys.readouterr().err


def test_main_requires_input_without_mic(capsys):
    with pytest.raises(SystemExit):
        proto.main(["--no-vmic"])  # Linux CI has no pactl: skip the virtual-mic step so the input check is what fires


def test_list_mics(monkeypatch, capsys):
    monkeypatch.setattr("tinglongwu.reverse.live.list_mic_names", lambda include_loopback=False: ["A", "B"])
    assert proto.main(["--list-mics"]) == 0
    assert capsys.readouterr().out.split() == ["A", "B"]


class FakeSeg:
    epoch = 0

    def segments(self, frames):
        yield from frames


class FakeSrc:
    def __init__(self, frames):
        self._f = frames

    def frames(self):
        yield from self._f


def test_capture_segments_yields_then_ends():
    segs = [np.ones(10, np.float32), np.ones(5, np.float32)]
    out = list(capture_segments(FakeSrc(segs), FakeSeg(), threading.Event()))
    assert [len(x) for x in out] == [10, 5]


def test_capture_error_propagates():
    class Boom:
        def frames(self):
            raise RuntimeError("no mic")
            yield

    with pytest.raises(RuntimeError, match="no mic"):
        list(capture_segments(Boom(), FakeSeg(), threading.Event()))


def test_recognize_filters_silence_and_hallucination():
    loud = np.full(100, 0.3, np.float32)
    asr = SimpleNamespace(transcribe=lambda a: SimpleNamespace(text="你好", no_speech=0.0))
    assert list(recognize_utterances([np.zeros(100, np.float32), loud], asr)) == ["你好"]
    asr2 = SimpleNamespace(transcribe=lambda a: None)
    assert list(recognize_utterances([loud], asr2)) == []
    asr3 = SimpleNamespace(transcribe=lambda a: SimpleNamespace(text="字幕由 Amara", no_speech=0.0))
    assert list(recognize_utterances([loud], asr3)) == []


class FakeMT:
    history: list = []
    _pair = None

    def translate(self, text, src, tgt):
        return SimpleNamespace(text=f"privet-{len(text)}" if tgt == "ru" else "zh-back")


class FakeTTS:
    def speak(self, text):
        yield {"t": "chunk", "sr": 24000}, np.ones(4, np.float32).tobytes()
        yield {"t": "done"}, None


class FakePlayer:
    enabled = True

    def __init__(self):
        self.pushed = []

    def push(self, audio, sr):
        self.pushed.append((len(audio), sr))


def test_live_wiring_auto_and_confirm(monkeypatch):
    """mic items -> translate -> (auto|confirm) -> TTS -> player, with mocked capture/ASR."""
    monkeypatch.setattr("tinglongwu.reverse.live.capture_segments", lambda src, seg, stop, lat=None: iter([1, 2]))
    monkeypatch.setattr("tinglongwu.reverse.live.recognize_utterances", lambda segs, asr, gate=None: iter(["你好", "再見"]))
    monkeypatch.setattr("tinglongwu.capture.mic.MicSource", lambda name: object())
    a = SimpleNamespace(mic_name="Headset Mic", chunk_s=10.0)
    for confirm, keys in ((False, None), (True, iter(["", "r"]))):
        res = {"items": []}
        pl, pv = FakePlayer(), FakePlayer()
        pv.enabled = False
        if confirm:
            monkeypatch.setattr("builtins.input", lambda *x: next(keys))
        proto._live_main(a, None, FakeMT(), FakeTTS(), pl, pv, confirm, res)
        if confirm:
            assert [r["decision"] for r in res["items"]] == ["sent", "discarded"]
        else:
            assert [r["decision"] for r in res["items"]] == ["sent", "sent"]
        assert res["items"][0]["ru"] == "privet-2"
    assert pl.enabled is False


def test_tts_worker_ignores_sigint():
    """Terminal Ctrl+C hits the TTS worker child too; it must not die with a traceback."""
    import inspect

    from tinglongwu.reverse import tts_worker

    src = inspect.getsource(tts_worker.main)
    assert "SIGINT" in src and "SIG_IGN" in src


def test_main_swallows_second_ctrl_c(monkeypatch, capsys):
    def boom(argv, vm):
        raise KeyboardInterrupt

    monkeypatch.setattr(proto, "_main", boom)
    assert proto.main([]) == 130
    assert "stopping" in capsys.readouterr().out
