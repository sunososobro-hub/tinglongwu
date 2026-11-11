"""#207: streamed TTS pieces (short first clause, growing pieces) and the gap meter / stream line."""
import importlib.util
from pathlib import Path

import numpy as np

from tinglongwu.reverse import proto
from tinglongwu.reverse.logic import GapMeter, Lat, speech_pieces, split_sentences, stream_line

LONG = ("Today we will look at the quarterly numbers and then talk about hiring for the new office in Taipei, "
        "which opens next spring, and after that we will review the budget for marketing and the launch timeline.")


def _norm(s):
    return "".join(s.split())


def test_first_piece_is_a_short_clause_and_nothing_is_lost():
    p = speech_pieces(LONG)
    assert len(p) >= 2 and len(p[0]) <= 40
    assert p[0] == "Today we will look at the quarterly"  # no comma within 40 chars -> last space
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
