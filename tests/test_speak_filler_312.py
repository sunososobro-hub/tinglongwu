"""#312: speak mode shows Traditional Chinese (same zh_convert as listen), drops filler-only utterances, and exits
without soundcard's CoUninitialize AttributeError."""
import types

import numpy as np
import pytest

from tinglongwu.asr import Transcript
from tinglongwu.capture import _soundcard as scmod
from tinglongwu.reverse.gate import DropReport, SegmentStats, gate
from tinglongwu.reverse.live import recognize_utterances

GOOD = dict(duration=1.5, rms=0.05, no_speech=0.05, avg_logprob=-0.3)
LOUD = np.full(16000, 0.1, np.float32)


class Asr:
    def __init__(self, texts):
        self.trs = iter(Transcript(t, "zh", 0.05, -0.3) for t in texts)

    def transcribe(self, seg):
        return next(self.trs)


@pytest.mark.parametrize("text", ["蛤蛤蛤蛤蛤", "他他他他他他 他", "嗯嗯", "嗯，啊。", "哈哈哈", "uh um", "Hmm, hmm.",
                                  "go go go go"])
def test_filler_only_utterances_are_dropped(text):
    assert gate(SegmentStats(text=text, **GOOD)) == (False, "filler")


@pytest.mark.parametrize("text", ["謝謝", "對對對", "好好", "哈哈，好的", "嗯，我們開始吧", "他說沒關係",
                                  "Oh no, the budget.", "Thank you"])
def test_short_real_sentences_pass(text):
    assert gate(SegmentStats(text=text, **GOOD)) == (True, "")


def test_filler_drop_is_counted_in_gate_line():
    lines = []
    rep = DropReport(out=lines.append, clock=lambda: 0.0)
    out = list(recognize_utterances([LOUD, LOUD, LOUD], Asr(["蛤蛤蛤蛤蛤", "他他他他他他 他", "我們開始吧"]),
                                    report=rep))
    assert out == ["我們開始吧"]
    rep.report(force=True)
    assert lines[0] == "speak gate: dropped 1 segment(s) (filler=1)"  # first drop is reported at once
    assert lines[-1] == "speak gate: dropped 1 segment(s) (filler=1)"  # the second, forced


def test_source_line_converted_like_listen_mode():
    out = list(recognize_utterances([LOUD, LOUD], Asr(["没关系，确认一下", "我说话的时候"]), zh_variant="tw"))
    assert out == ["沒關係，確認一下", "我說話的時候"]
    # #289 protected phrases go through the same zh_convert
    assert list(recognize_utterances([LOUD], Asr(["音乐家具有更好的记忆"]), zh_variant="tw")) == ["音樂家具有更好的記憶"]
    assert list(recognize_utterances([LOUD], Asr(["沒關係"]), zh_variant="cn")) == ["没关系"]
    assert list(recognize_utterances([LOUD], Asr(["没关系"]))) == ["没关系"]  # no variant: untouched


def test_proto_zh_variant_flag_defaults_to_tw_and_reaches_recognizer(monkeypatch):
    from tinglongwu.reverse import proto

    assert proto.build_parser().parse_args([]).zh_variant == "tw"
    assert proto.build_parser().parse_args(["--zh-variant", "cn"]).zh_variant == "cn"
    seen = {}

    def fake_rec(segs, asr, gate=None, src="zh", **kw):
        seen.update(kw)
        return iter([])

    monkeypatch.setattr("tinglongwu.reverse.live.capture_segments", lambda *a, **k: iter([]))
    monkeypatch.setattr("tinglongwu.reverse.live.recognize_utterances", fake_rec)
    monkeypatch.setattr("tinglongwu.capture.mic.MicSource", lambda name: object())
    a = types.SimpleNamespace(mic_name="m", chunk_s=6.0, zh_variant="cn")
    pl = types.SimpleNamespace(enabled=True)
    proto._live_main(a, None, None, None, pl, types.SimpleNamespace(enabled=False), False, {"items": []})
    assert seen["zh_variant"] == "cn"


def _fake_mediafoundation():
    """Same shape as soundcard.mediafoundation: _COMLibrary.__del__ reads the module-global _ole32."""
    mod = types.ModuleType("fake_mf")
    exec("class _COMLibrary:\n"
         "    com_loaded = True\n"
         "    def __del__(self):\n"
         "        if self.com_loaded:\n"
         "            _ole32.CoUninitialize()\n", mod.__dict__)
    calls = []
    mod._ole32 = types.SimpleNamespace(CoUninitialize=lambda: calls.append("uninit"))
    return mod, calls


def test_com_teardown_at_shutdown_is_silent():
    mf, calls = _fake_mediafoundation()
    com = mf._COMLibrary()
    mf._ole32 = None  # what interpreter shutdown does to module globals
    with pytest.raises(AttributeError):
        com.__del__()  # unpatched: the traceback the owner saw
    assert scmod.patch_com_teardown(mf) and scmod.patch_com_teardown(mf)  # idempotent
    com.__del__()  # patched: no error
    mf._ole32 = types.SimpleNamespace(CoUninitialize=lambda: calls.append("uninit"))
    com.__del__()
    assert calls == ["uninit"]  # normal teardown still releases COM


def test_patch_mixformat_also_patches_teardown():
    mf, _ = _fake_mediafoundation()
    scmod.patch_mixformat(mf)
    assert getattr(mf._COMLibrary, "_tlwu_teardown", False)
