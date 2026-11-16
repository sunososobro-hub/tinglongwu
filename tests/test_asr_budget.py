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


def test_asr_budget_timeout_keeps_decoded_part_and_next_one_runs(capsys):
    # 1 s of audio -> budget max(4, 3 x 1) = 4 s; the decoder spends 3 s per piece and has 5 pieces (15 s).
    # #1252: the pieces decoded before the stop are kept (lost source text cannot be recovered); no drop.
    r = rec([["a", "b", "c", "d", "e"], ["Next sentence."]], cost=3.0)
    t0 = r.clock.t
    tr = r.transcribe(sec(1))
    assert tr is not None and tr.text == "a b"  # "b" crossed 4 s but was already decoded
    assert r.clock.t - t0 == pytest.approx(6.0)  # stopped pulling after the piece that crossed 4 s
    err = capsys.readouterr().err
    assert "cut a sentence at its time budget" in err and "1 cut so far" in err
    assert "dropped a sentence" not in err and getattr(r, "dropped", 0) == 0
    r.model.cost = 0.1
    tr = r.transcribe(sec(1))
    assert tr is not None and tr.text == "Next sentence."
    assert r.cut == 1


def test_asr_budget_cut_log_names_the_kept_seconds(capsys):
    # #1252: the log says how much of the sentence survived, so a field log shows the lost tail
    r = rec([["first", "second", "third"]], cost=3.0)
    ends = iter([7.5, 15.0, 20.0])
    first = r.model.transcribe

    def timed(audio, **kw):
        gen, info = first(audio, **kw)

        def g():
            for s in gen:
                s.end = next(ends)
                yield s
        return g(), info
    r.model.transcribe = timed
    tr = r.transcribe(sec(20))  # budget 60 s: not cut
    assert tr.text == "first second third"
    r.model.outputs = [["a", "b", "c", "d", "e"]]
    r.model.cost = 30.0
    ends = iter([6.0, 12.0, 18.0, 24.0, 30.0])
    tr = r.transcribe(sec(24))  # budget 72 s: "a" at 30, "b" at 60, "c" at 90 crosses -> a b c kept
    assert tr.text == "a b c"
    assert "kept 18.0 of 24.0 s" in capsys.readouterr().err


def test_asr_budget_drops_only_when_nothing_was_decoded(capsys):
    # #1252: a drop is left only for a decode that produced no usable text before the deadline
    r = rec([["noise"]], cost=5.0)
    gen0 = r.model.transcribe

    def silent(audio, **kw):
        gen, info = gen0(audio, **kw)

        def g():
            for s in gen:
                s.no_speech_prob, s.avg_logprob = 0.9, -2.0  # filtered as no-speech
                yield s
        return g(), info
    r.model.transcribe = silent
    assert r.transcribe(sec(1)) is None
    assert r.dropped == 1 and "dropped a sentence over its time budget" in capsys.readouterr().err


def test_asr_budget_skips_loop_retry_when_time_is_up():
    r = rec([[LOOP], ["clean retry"]], cost=5.0)
    tr = r.transcribe(sec(1))
    assert len(r.model.calls) == 1  # no retry after the budget ran out
    assert tr is not None and tr.text  # #1252: the collapsed first pass is kept, not dropped


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


class LateClockModel(SlowModel):
    """Fake decoder whose cost lands when the generator is exhausted: the decode finishes past the deadline."""

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        pieces = self.outputs.pop(0)

        def gen():
            yield from (Seg(p) for p in pieces)
            self.clock.t += self.cost
        return gen(), type("I", (), {"language": "en"})()


def late(outputs, cost):
    r = rec(outputs)
    r.model = LateClockModel(r.clock, outputs, cost)
    return r


def test_asr_budget_finished_decode_past_deadline_keeps_text(capsys):
    # #1252: 1 s audio -> 4 s budget; the decode finishes at 10 s. The text is already there: return it.
    r = late([["Long conclusion.", "Second half."]], cost=10.0)
    tr = r.transcribe(sec(1))
    assert tr is not None and tr.text == "Long conclusion. Second half."
    assert getattr(r, "dropped", 0) == 0
    assert "dropped a sentence" not in capsys.readouterr().err


def test_asr_budget_over_deadline_skips_retry_but_keeps_first_pass():
    # #1252: a looped first pass that finished past the deadline is collapsed and returned, with no retry
    r = late([[LOOP], ["clean retry"]], cost=10.0)
    tr = r.transcribe(sec(1))
    assert tr is not None and tr.text  # the collapsed loop, not a drop
    assert len(r.model.calls) == 1 and getattr(r, "dropped", 0) == 0


def test_asr_budget_retry_overrun_keeps_first_pass():
    # #1252: first pass in time, the loop retry runs past the deadline mid-decode -> first pass is kept
    r = rec([[LOOP], ["x", "y", "z"]], cost=0.1)
    r.model.outputs = [[LOOP], ["x", "y", "z"]]
    first = r.model.transcribe

    def costly(audio, **kw):
        if r.model.calls:  # second call (retry): 3 s per piece
            r.model.cost = 3.0
        return first(audio, **kw)
    r.model.transcribe = costly
    tr = r.transcribe(sec(1))
    assert tr is not None and tr.text and len(r.model.calls) == 2
    assert getattr(r, "dropped", 0) == 0


def _with_ends(r, ends):
    """Give each yielded fake segment an `end` (seconds into the audio), like faster-whisper's timestamps."""
    first, it = r.model.transcribe, iter(ends)

    def timed(audio, **kw):
        gen, info = first(audio, **kw)

        def g():
            for s in gen:
                s.end = next(it)
                yield s
        return g(), info
    r.model.transcribe = timed


def test_asr_budget_cut_holds_the_undecoded_tail(capsys):
    # #1252 step 3: 24 s, budget 72 s; "a b c" decoded up to 18 s, then the cut. The last 6 s are held for the caller.
    r = rec([["a", "b", "c", "d", "e"]], cost=30.0)
    _with_ends(r, [6.0, 12.0, 18.0, 24.0, 30.0])
    audio = np.arange(int(24 * asr.SAMPLE_RATE), dtype=np.float32)
    assert r.transcribe(audio).text == "a b c"
    tail = r.pop_tail()
    assert tail is not None and len(tail) == 6 * asr.SAMPLE_RATE
    assert tail[0] == 18 * asr.SAMPLE_RATE  # starts exactly where the kept text ended
    assert r.pop_tail() is None  # cleared on read
    assert "6.0 s queued again" in capsys.readouterr().err


def test_asr_budget_no_tail_without_cut_or_progress():
    r = rec([["Hello there."]])
    r.transcribe(sec(2))
    assert r.pop_tail() is None  # not cut
    r = rec([["a", "b"]], cost=30.0)
    _with_ends(r, [0.0, 0.0])
    r.transcribe(sec(5))  # cut, but the kept text ends at 0 s: re-queueing the whole input could loop forever
    assert r.cut == 1 and r.pop_tail() is None
    r = rec([["a", "b"]], cost=30.0)
    _with_ends(r, [4.8, 5.0])
    r.transcribe(sec(5))  # cut with a 0.2 s tail: too short to decode
    assert r.pop_tail() is None
    assert asr.Recognizer.pop_tail(r) is None  # base default: never cut
