"""#207: streamed TTS pieces (short first clause, growing pieces) and the gap meter / stream line."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from tinglongwu.reverse import proto
from tinglongwu.reverse.logic import GapMeter, Lat, speech_pieces, split_sentences, stream_line

LONG = ("Today we will look at the quarterly numbers and then talk about hiring for the new office in Taipei, "
        "which opens next spring, and after that we will review the budget for marketing and the launch timeline.")


def _norm(s):
    return "".join(s.split())


def test_first_piece_is_a_short_clause_and_nothing_is_lost():
    p = speech_pieces(LONG)
    assert len(p) >= 2 and len(p[0]) <= 24
    assert p[0] == "Today we will look at"  # no comma within 24 chars -> last space
    assert _norm(" ".join(p)) == _norm(LONG)


def test_first_piece_prefers_a_clause_mark():
    p = speech_pieces("Good morning, everyone, today we start the quarterly review of all teams in the company.")
    assert p[0] == "Good morning, everyone,"


def test_pieces_grow_at_most_by_the_factor():
    p = speech_pieces(LONG + " " + LONG, first_chars=40, grow=3)
    for a, b in zip(p, p[1:]):
        assert len(b) <= max(40, 3 * len(a)), (a, b)
    assert all(len(x) <= 220 for x in p)


def test_short_text_is_one_piece_and_sentences_stay_whole():
    assert speech_pieces("Hello there.") == ["Hello there."]
    assert speech_pieces("Привет. Как дела? Хорошо!") == split_sentences("Привет. Как дела? Хорошо!")
    assert speech_pieces("") == [] and speech_pieces(" ... ") == []
    assert speech_pieces("今日は晴れです。明日は雨です。") == ["今日は晴れです。", "明日は雨です。"]


def test_gap_meter():
    t = iter([0.0, 1.0, 3.5])
    g = GapMeter(lambda: next(t))
    assert g.push(2.0) == 0.0  # plays 0.0-2.0
    assert g.push(2.0) == 0.0  # arrives at 1.0, queued: plays 2.0-4.0
    assert g.push(1.0) == 0.0 and g.max == 0.0  # arrives 3.5 < 4.0
    t = iter([0.0, 2.5])
    g = GapMeter(lambda: next(t))
    g.push(2.0)
    assert g.push(1.0) == 0.5 and g.max == 0.5


def test_stream_line():
    assert stream_line(3, 0.0, [{"gen_s": 0.5, "audio_s": 2.0}, {"gen_s": 1.0, "audio_s": 4.0}]) == \
        "stream pieces=3 gap_max=0.00s rtf=0.25"
    assert stream_line(1, 0.123, [{}]) == "stream pieces=1 gap_max=0.12s rtf=-"


class _MT:
    def translate(self, text, src, tgt):
        return type("R", (), {"text": LONG})()


class _TTS:
    def __init__(self):
        self.texts = []

    def speak(self, text, lang=None):
        self.texts.append(text)
        n = 16 * len(text) * 24000 // 1000  # ~16 chars/s
        yield {"t": "chunk", "sr": 24000}, np.zeros(n, np.float32).tobytes()
        yield {"t": "done", "gen_s": 0.25 * n / 24000, "audio_s": n / 24000}, None


class _Player:
    def __init__(self):
        self.n = 0

    def push(self, audio, sr):
        self.n += 1


def test_live_auto_streams_first_clause_and_reports_gap(capsys):
    tts, player, res = _TTS(), _Player(), {"items": []}
    proto._run_auto_live(iter([(1, "mic", "你好")]), _MT(), tts, player, res, "en", Lat())
    # before #207 the whole first sentence (209 chars) was the first TTS request
    assert len(tts.texts[0]) <= 40 and len(tts.texts) == player.n >= 2
    it = res["items"][0]
    assert it["pieces"] == player.n and it["gap_max"] >= 0.0
    err = capsys.readouterr().err
    assert f"stream pieces={player.n} gap_max=" in err and "rtf=0.25" in err


def _selftest():
    spec = importlib.util.spec_from_file_location(
        "speak_selftest", Path(__file__).resolve().parent.parent / "scripts" / "speak_selftest.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_selftest_parses_stream_lines_and_fails_on_gaps():
    st = _selftest()
    log = ("   [vad 0.60 | stt 1.00 | mt 0.20 | tts 0.80 | total 2.60 s]\n"
           "latency asr=1.0s mt=1.2s first_audio=2.0s done=3.0s\nstream pieces=3 gap_max=0.10s rtf=0.30\n"
           "   [vad 0.60 | stt 1.20 | mt 0.20 | tts 1.00 | total 3.00 s]\n"
           "latency asr=1.2s mt=1.4s first_audio=2.4s done=4.0s\nstream pieces=2 gap_max=0.45s rtf=1.10\n")
    p = st.parse_log(log)
    assert [d["gap_max"] for d in p["streams"]] == [0.1, 0.45] and p["streams"][1]["rtf"] == 1.1
    assert st.stage_table(p["latencies"])["tts"] == {"p50": 0.9, "max": 1.0}
    assert st.stream_gap_reason(p["streams"]) == "stream gap 0.45s > 0.30s"
    assert st.stream_gap_reason(p["streams"][:1]) is None


# ---------------------------------------------------------------- #207 part 2: adaptive sizing, first-audio timing
from types import SimpleNamespace  # noqa: E402

from tinglongwu.reverse.logic import PiecePlanner, next_budget  # noqa: E402


def test_planner_budget_is_clamped_and_never_spans_sentences():
    text = "Good morning, everyone. " + LONG
    pl = PiecePlanner(text, first_chars=24, max_chars=220)
    first = pl.next()
    assert first == "Good morning, everyone."  # a whole short sentence; nothing of the next one
    second = pl.next(5)
    assert len(second) <= 12  # below min_chars -> min_chars
    rest = [second]
    while (p := pl.next(10_000)) is not None:  # above max_chars -> max_chars
        rest.append(p)
        assert len(p) <= 220
    assert pl.next() is None and _norm(first + "".join(rest)) == _norm(text)
    # nothing lost or reordered, whatever the budgets were
    pl, got, budgets = PiecePlanner(text), [], iter([None, 30, 8, 500, 40, 100, 60, 200, 25] * 4)
    while (p := pl.next(next(budgets))) is not None:
        got.append(p)
    assert _norm("".join(got)) == _norm(text)


def test_next_budget():
    assert next_budget(2.0, None) is None and next_budget(2.0, 0) is None  # speed unknown: static rule
    assert next_budget(2.0, 40.0, safety=0.5) == 40
    assert next_budget(-1.0, 40.0) == 0  # nothing queued (planner raises it to first_chars)


class _FakeClockTTS:
    """Synthesis takes len(text)/cps seconds of fake time; audio is 16 chars/s (~speech rate)."""

    def __init__(self, cps=40.0, first_extra=0.0):
        self.now, self.cps, self.texts, self.first_extra = 0.0, cps, [], first_extra

    def clock(self):
        return self.now

    def speak(self, text, lang=None):
        self.texts.append(text)
        self.now += len(text) / self.cps + (self.first_extra if len(self.texts) == 1 else 0.0)
        n = int(len(text) / 16 * 24000)
        yield {"t": "chunk", "sr": 24000}, np.zeros(n, np.float32).tobytes()
        yield {"t": "done", "gen_s": len(text) / self.cps, "audio_s": n / 24000}, None


def _stream(tts, text):
    """Consume like _run_auto_live: push each piece at once; return (pieces, first, GapMeter on the fake clock)."""
    first, gaps = [], GapMeter(tts.clock)
    pieces = []
    for audio, sr in proto._synth_sentences(tts, text, clock=tts.clock, on_first=first.append):
        pieces.append(audio)
        gaps.push(len(audio) / sr)
    return pieces, first, gaps


def test_first_audio_callback_fires_once_after_the_short_first_piece():
    tts = _FakeClockTTS(cps=40.0)
    pieces, first, _ = _stream(tts, LONG)
    assert len(first) == 1 and len(tts.texts[0]) <= 24
    assert first[0] == pytest.approx(len(tts.texts[0]) / 40.0)  # only the first piece's synthesis, not the sentence
    assert first[0] < len(split_sentences(LONG)[0]) / 40.0 / 4
    assert len(pieces) == len(tts.texts) and _norm(" ".join(tts.texts)) == _norm(LONG)  # in order, nothing lost


def test_live_sizing_leaves_no_gap_and_grows_pieces():
    tts = _FakeClockTTS(cps=40.0)  # ~2.5x faster than real time
    _, _, gaps = _stream(tts, LONG + " " + LONG)
    assert gaps.max == 0.0
    assert max(len(t) for t in tts.texts[1:]) > 3 * len(tts.texts[0])  # sized by the queue, not a fixed 3x
    # a slow worker (RTF ~0.8): the static 3x rule would leave gaps; live sizing keeps the next piece small enough
    tts = _FakeClockTTS(cps=20.0)
    _, _, gaps = _stream(tts, LONG)
    assert gaps.max == 0.0
    t, end, worst = 0.0, None, 0.0
    for p in speech_pieces(LONG):
        t += len(p) / 20.0
        worst = max(worst, 0.0 if end is None else t - end)
        end = max(t, end or t) + len(p) / 16
    assert worst > 0.3  # what the old fixed rule did on the same worker


def test_slow_first_piece_does_not_make_the_next_one_huge():
    tts = _FakeClockTTS(cps=40.0, first_extra=2.0)  # first request pays a one-off cost
    _stream(tts, LONG)
    assert len(tts.texts[1]) <= 40  # speed measured pessimistically from the slow piece


def test_confirm_mode_reports_first_part_not_whole_utterance(monkeypatch, capsys):
    import threading
    import time

    release = threading.Event()

    class TwoPartTTS:
        engine = "none"

        def speak(self, text):
            if not release.is_set():  # first piece: at once
                release.set()
            else:  # later pieces: slow
                time.sleep(0.3)
            yield {"t": "chunk", "sr": 24000}, np.ones(4, np.float32).tobytes()
            yield {"t": "done"}, None

    class P:
        enabled = True

        def push(self, a, sr):
            pass

    monkeypatch.setattr("builtins.input", lambda *x: "y")
    res = {"items": []}
    proto._run_confirm(SimpleNamespace(to="en", no_back=True), iter([(1, "mic", "你好")]), _MT(), TwoPartTTS(), P(),
                       SimpleNamespace(enabled=False), res)
    it = res["items"][0]
    assert it["decision"] == "sent" and it["lat"]["tts"] < 0.25  # whole utterance took >= 0.3 s
    assert "tts_first " in capsys.readouterr().err


def test_warm_tts_speaks_once_in_the_target_language_and_never_raises():
    seen = []

    class T:
        def speak(self, text):
            seen.append(text)
            yield {"t": "done"}, None

    proto.warm_tts(T(), "ru")
    proto.warm_tts(T(), "follow")
    assert seen == [proto.WARM_TEXT["ru"], proto.WARM_EN]

    class Broken:
        def speak(self, text):
            raise RuntimeError("worker gone")

    proto.warm_tts(Broken(), "en")


def test_selftest_parses_the_tts_first_label():
    st = _selftest()
    p = st.parse_log("   [vad 0.60 | stt 1.00 | mt 0.20 | tts_first 0.40 | total 2.20 s]\n"
                     "   [vad 0.60 | stt 1.00 | mt 0.20 | tts_first - | total 1.80 s]\n")
    assert [d["tts"] for d in p["latencies"]] == [0.4, None]
