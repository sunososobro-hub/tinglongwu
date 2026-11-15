"""#968 Acceptance (1)/(3): the speak noise-gate eval harness (tlwu_eval.speak_noise).

The real run needs Whisper + the FLEURS zh clips (eval-langs zh->en job); here the recognizer is scripted so the
harness itself is checked: synthetic noise clips, the GUI speak segmenter + gate, PASS/FAIL and the threshold table.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from tinglongwu.asr import Transcript
from tlwu_eval import speak_noise as sn


class _Scripted:
    """Returns the same Whisper output for every non-silent segment."""

    def __init__(self, text, no_speech, logprob, lang="zh"):
        self.out = Transcript(text, lang, no_speech, logprob)
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        return self.out if len(audio) and float(np.max(np.abs(audio))) > 0 else None


def _args():
    return sn.speak_args("zh", ["--no-vad"])  # fixed blocks: the scripted ASR has nothing for VAD to find


@pytest.mark.parametrize("kind", sn.NOISE)
def test_speak_noise_gate_clips_are_deterministic(kind):
    a, b = sn.synth(kind), sn.synth(kind)
    assert a.dtype == np.float32 and len(a) == int(sn.SECONDS[kind] * sn.SR)
    assert np.array_equal(a, b)
    assert float(np.max(np.abs(a))) <= 1.0


def test_speak_noise_gate_clips_look_like_their_noise():
    kb = sn.synth("keyboard")
    assert float(np.max(np.abs(kb))) > 0.05  # loud key transients, not just room tone
    frames = np.abs(kb[: len(kb) // 160 * 160]).reshape(-1, 160).max(axis=1)  # 10 ms frames
    assert 60 < int(np.sum(frames > 0.05)) < 1500  # clicks: sparse, not a steady tone
    fan = sn.synth("fan")
    assert not np.any(fan[: 5 * sn.SR]) and float(np.sqrt(np.mean(fan[6 * sn.SR:] ** 2))) > 1e-3


def test_speak_noise_gate_eval_drops_hallucinated_noise_line():
    clip = {"kind": "keyboard", "id": "keyboard_synth", "audio": sn.synth("keyboard")}
    # what Whisper made of the owner's typing: fluent text, high no_speech, low logprob
    asr = _Scripted("Today is a fun day.", 0.72, -1.25, "en")
    r = sn.run_clip(clip, asr, _args())
    assert asr.calls > 0 and r["segments"]
    assert r["sent"] == []
    assert {s["reason"] for s in r["segments"] if s["text"]} <= {"no_speech", "low_logprob"}


def test_speak_noise_gate_eval_sends_clear_speech():
    t = np.arange(3 * sn.SR) / sn.SR
    clip = {"kind": "speech", "id": "zh_001", "audio": (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)}
    r = sn.run_clip(clip, _Scripted("你好，聽得到嗎？", 0.05, -0.3), _args())
    assert r["sent"] and all(s["reason"] == "" for s in r["segments"])


def _clip(kind, cid, sent, *segs):
    return {"id": cid, "kind": kind, "sent": sent,
            "segments": [{"seconds": 2.0, "rms": 0.01, "text": tx, "no_speech": ns, "avg_logprob": lp,
                          "reason": why} for tx, ns, lp, why in segs]}


def test_speak_noise_gate_report_pass_fail_and_thresholds():
    noise = _clip("keyboard", "keyboard_synth", [], ("Notification.", 0.7, -1.4, "no_speech"))
    speech = [_clip("speech", f"zh_{i:03}", ["好"], ("你好", 0.1, -0.4 - i / 100, "")) for i in range(10)]
    rep = sn.report([noise, *speech], 9)
    assert rep["stage"] == "PASS" and rep["noise_lines_sent"] == 0 and rep["speech_clips_sent"] == 10
    th = rep["thresholds"]
    assert th["speech_logprob_min"] == pytest.approx(-0.49) and th["noise_logprob_max"] == -1.4
    assert th["logprob_margin"] == pytest.approx(0.91) and th["no_speech_margin"] == pytest.approx(0.6)
    json.dumps(rep)  # the CI step writes it as JSON
    assert "PASS" in sn.summary(rep)

    leaked = _clip("mouse", "mouse_synth", ["Thank you."], ("Thank you.", 0.3, -0.6, ""))
    assert sn.report([leaked, *speech], 9)["stage"] == "FAIL"
    few = [dict(c, sent=[]) for c in speech[:2]] + speech[2:]
    assert sn.report([noise, *few], 9)["stage"] == "FAIL"  # 8/10 speech clips sent < 9
