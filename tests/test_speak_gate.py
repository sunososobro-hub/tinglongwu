"""#249: speak-side input gate, bounded/expiring confirm queue, rate-limited drop summary."""
from types import SimpleNamespace

import numpy as np
import pytest

from tinglongwu.asr import Transcript
from tinglongwu.reverse.confirm import ConfirmSession, Utterance
from tinglongwu.reverse.gate import DropReport, SegmentStats, gate
from tinglongwu.reverse.live import recognize_utterances

GOOD = dict(duration=1.5, rms=0.05, text="請把保密協議寄給大家", no_speech=0.05, avg_logprob=-0.3)


def test_normal_sentence_passes():
    assert gate(SegmentStats(**GOOD)) == (True, "")
    assert gate(SegmentStats(**{**GOOD, "text": "Let's get started with the budget."})) == (True, "")


@pytest.mark.parametrize("change,reason", [
    ({"duration": 0.1}, "too_short"),
    ({"rms": 2e-4}, "quiet"),
    ({"no_speech": 0.8}, "no_speech"),
    ({"avg_logprob": -1.4}, "low_logprob"),
    ({"text": "嗯"}, "few_chars"),
    ({"text": "。。"}, "few_chars"),
    ({"text": "對對對對對對對對對對對對對對對對對對對對對對對對對對對對對對對對對對對對對對對對"}, "compression"),
    ({"text": "我們走吧 我們走吧 我們走吧 我們走吧"}, "repeat"),
    ({"text": "字幕由Amara.org社區提供"}, "repeat"),
])
def test_each_reject_reason(change, reason):
    assert gate(SegmentStats(**{**GOOD, **change})) == (False, reason)


def test_thresholds_overridable_by_env():
    s = SegmentStats(**{**GOOD, "duration": 0.5})
    assert gate(s)[0]
    assert gate(s, env={"TLWU_GATE_MIN_DUR": "1.0"}) == (False, "too_short")
    assert gate(s, env={"TLWU_GATE_MIN_DUR": "junk"})[0]  # unparsable: default kept


class Asr:
    def __init__(self, trs):
        self.trs, self.calls = iter(trs), 0

    def transcribe(self, seg):
        self.calls += 1
        return next(self.trs)


def test_recognize_applies_gate_and_skips_asr_for_short_segments():
    rep_lines = []
    t = [0.0]
    rep = DropReport(out=rep_lines.append, clock=lambda: t[0])
    loud, blip = np.full(16000, 0.1, np.float32), np.full(1600, 0.1, np.float32)
    asr = Asr([Transcript("嗯", "zh"), Transcript("我們開始吧", "zh", 0.9, -0.2),
               Transcript("我們開始吧", "zh", 0.1, -1.5), Transcript("請把報告寄給我", "zh", 0.1, -0.4)])
    out = list(recognize_utterances([blip, loud, loud, loud, loud], asr, report=rep))
    assert out == ["請把報告寄給我"]
    assert asr.calls == 4  # the 0.1 s blip never reached Whisper
    assert rep_lines == ["speak gate: dropped 1 segment(s) (too_short=1)"]  # first drop at once, then rate-limited
    t[0] = 61.0
    rep.report()
    assert rep_lines[-1] == "speak gate: dropped 3 segment(s) (few_chars=1, low_logprob=1, no_speech=1)"


def test_drop_summary_at_most_once_per_minute():
    lines, t = [], [0.0]
    rep = DropReport(out=lines.append, clock=lambda: t[0])
    for i in range(100):
        t[0] = i * 0.5  # 100 drops in 50 s
        rep.add("quiet")
    assert len(lines) == 1
    t[0] = 60.0
    rep.add("repeat")
    assert len(lines) == 2 and lines[1] == "speak gate: dropped 100 segment(s) (quiet=99, repeat=1)"


def _session(log, keys, clock, **kw):
    it = iter(keys)
    return ConfirmSession(lambda ru: (np.ones(2, np.float32), 24000), lambda a, sr: None, out=log.append,
                          readline=lambda: next(it), flush=lambda: None, clock=clock, **kw)


def test_confirm_queue_full_drops_oldest():
    log = []
    s = _session(log, ["y"] * 3, lambda: 0.0, max_pending=3)
    us = [Utterance(i, f"z{i}", f"r{i}") for i in range(1, 6)]
    for u in us:
        s.submit(u)
    s.finish()
    s.run()
    assert [u.decision for u in us] == ["dropped", "dropped", "sent", "sent", "sent"]
    assert [m for m in log if m.startswith("dropped")] == ["dropped (queue full): z1", "dropped (queue full): z2"]


def test_confirm_queue_expired_sentence_dropped():
    log, t = [], [0.0]
    s = _session(log, ["y"], lambda: t[0], max_age=30.0)
    old, new = Utterance(1, "z1", "r1"), Utterance(2, "z2", "r2")
    s.submit(old)
    t[0] = 25.0
    s.submit(new)
    t[0] = 31.0  # old waited 31 s, new 6 s
    s.finish()
    s.run()
    assert (old.decision, new.decision) == ("dropped", "sent")
    assert "dropped (expired after 30 s): z1" in log


def test_confirm_limits_from_env(monkeypatch):
    monkeypatch.setenv("TLWU_CONFIRM_MAX_PENDING", "5")
    monkeypatch.setenv("TLWU_CONFIRM_MAX_AGE", "12")
    s = ConfirmSession(lambda ru: None, lambda a, sr: None)
    assert (s.max_pending, s.max_age) == (5, 12.0)


def test_transcript_carries_avg_logprob_from_whisper():
    from tinglongwu import asr as asr_mod

    rec = asr_mod.FasterWhisperRecognizer.__new__(asr_mod.FasterWhisperRecognizer)
    seg = [SimpleNamespace(text="你好嗎", no_speech_prob=0.1, avg_logprob=-0.4),
           SimpleNamespace(text="我很好", no_speech_prob=0.2, avg_logprob=-0.6)]
    rec.model = SimpleNamespace(transcribe=lambda *a, **k: (iter(seg), SimpleNamespace(language="zh")))
    rec.lang, rec._last, rec.glossary, rec.candidates = "zh", "", None, None
    rec._lang_for = lambda audio, reuse=False: "zh"
    tr = rec.transcribe(np.zeros(16000, np.float32))
    assert tr.no_speech == pytest.approx(0.2) and tr.avg_logprob == pytest.approx(-0.5)
