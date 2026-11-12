"""#251: live ASR worst case is bounded - capped retries, token cap, per-sentence time budget. Fakes only."""
import numpy as np
import pytest

from tinglongwu import asr

LOOP = "и потом " * 12  # repeat.has_loop -> True


class Seg:
    def __init__(self, text):
        self.text, self.no_speech_prob, self.avg_logprob = text, 0.0, -0.2


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class SlowModel:
    """Fake faster-whisper: each yielded segment costs `cost` seconds on the fake clock (lazy, like the real one)."""

    def __init__(self, clock, outputs, cost=0.1):
        self.clock, self.outputs, self.cost, self.calls = clock, list(outputs), cost, []

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        pieces = self.outputs.pop(0)

        def gen():
            for p in pieces:
                self.clock.t += self.cost
                yield Seg(p)
        return gen(), type("I", (), {"language": "en"})()


def rec(outputs, live=True, cost=0.1):
    r = asr.FasterWhisperRecognizer.__new__(asr.FasterWhisperRecognizer)
    r.clock = Clock()
    r.model = SlowModel(r.clock, outputs, cost)
    r.lang, r._last, r.glossary, r.live = "en", "", None, live
    return r


def sec(s):
    return np.zeros(int(s * asr.SAMPLE_RATE), np.float32)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("TLWU_ASR_TEMP_PASSES", "TLWU_ASR_TOKENS_PER_S", "TLWU_ASR_BUDGET_FACTOR", "TLWU_ASR_BUDGET_MIN"):
        monkeypatch.delenv(k, raising=False)


def test_asr_retry_live_is_one_pass_and_at_most_one_loop_retry():
    r = rec([[LOOP], [LOOP]])
    r.transcribe(sec(2))
    assert len(r.model.calls) == 2  # first decode + one loop retry, never more
    assert [c["temperature"] for c in r.model.calls] == [(0.0,), (0.3,)]


def test_asr_retry_temp_passes_configurable(monkeypatch):
    monkeypatch.setenv("TLWU_ASR_TEMP_PASSES", "2")
    r = rec([["Hello there."]])
    r.transcribe(sec(2))
    assert r.model.calls[0]["temperature"] == (0.0, 0.2)


def test_asr_retry_token_cap_scales_with_audio_length():
    assert asr.token_cap(0.5) == asr.MIN_NEW_TOKENS
    assert asr.token_cap(5) == 5 * asr.TOKENS_PER_S
    assert asr.token_cap(60) == asr.MAX_NEW_TOKENS
    r = rec([["Hello there."]])
    r.transcribe(sec(5))
    assert r.model.calls[0]["max_new_tokens"] == 60


def test_asr_budget_timeout_drops_sentence_and_next_one_runs(capsys):
    # 1 s of audio -> budget max(4, 3 x 1) = 4 s; the decoder spends 3 s per piece and has 5 pieces (15 s)
    r = rec([["a", "b", "c", "d", "e"], ["Next sentence."]], cost=3.0)
    t0 = r.clock.t
    assert r.transcribe(sec(1)) is None  # dropped, not blocking
    assert r.clock.t - t0 == pytest.approx(6.0)  # stopped pulling after the piece that crossed 4 s
    err = capsys.readouterr().err
    assert "dropped a sentence over its time budget" in err and "1 dropped so far" in err
    r.model.cost = 0.1
    tr = r.transcribe(sec(1))
    assert tr is not None and tr.text == "Next sentence."
    assert r.dropped == 1


def test_asr_budget_skips_loop_retry_when_time_is_up():
    r = rec([[LOOP], ["clean retry"]], cost=5.0)
    assert r.transcribe(sec(1)) is None
    assert len(r.model.calls) == 1  # no retry after the budget ran out


def test_asr_budget_env_override(monkeypatch):
    monkeypatch.setenv("TLWU_ASR_BUDGET_MIN", "20")
    assert asr.time_budget(1) == 20
    monkeypatch.setenv("TLWU_ASR_BUDGET_FACTOR", "10")
    assert asr.time_budget(5) == 50


def test_asr_budget_normal_sentence_unchanged():
    r = rec([["Hello there.", "How are you?"]])
    tr = r.transcribe(sec(3))
    assert tr.text == "Hello there. How are you?" and tr.lang == "en"
    assert r._last == "Hello there. How are you?"


def test_asr_retry_file_mode_keeps_full_fallback_and_no_budget():
    r = rec([[LOOP], ["Clean retry."]], live=False, cost=100.0)  # very slow: file mode never drops
    tr = r.transcribe(sec(1))
    assert tr is not None and tr.text == "Clean retry."
    first, retry = r.model.calls
    assert first["temperature"] == (0.0, 0.2, 0.4) and retry["temperature"] == (0.3, 0.5, 0.7)
    assert "max_new_tokens" not in first and "max_new_tokens" not in retry
