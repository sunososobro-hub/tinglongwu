"""Per-segment language following: candidate selection, follow file, translator switching, zh passthrough."""
import numpy as np

from tinglongwu import asr as asr_mod
from tinglongwu.langfollow import (clear_lang, parse_candidates, pick_candidate, read_lang, write_lang)
from tinglongwu.pipeline import run
from tinglongwu.sink import Result, Sink
from tinglongwu.translate import PivotTranslator, Translation


class Seg:
    def __init__(self, t):
        self.text, self.no_speech_prob, self.avg_logprob = t, 0.0, -0.1


class FakeModel:
    def __init__(self, probs, text="hello there"):
        self.probs, self.text, self.langs, self.detects = probs, text, [], 0

    def detect_language(self, audio):
        self.detects += 1
        p = self.probs.pop(0)
        top = max(p.items(), key=lambda kv: kv[1])
        return top[0], top[1], list(p.items())

    def transcribe(self, audio, language=None, **kw):
        self.langs.append(language)
        return [Seg(self.text)], type("I", (), {"language": language})()


def make(probs, tmp_path=None, cands=("en", "ru", "zh")):
    r = asr_mod.FasterWhisperRecognizer.__new__(asr_mod.FasterWhisperRecognizer)
    r.model, r.lang, r._last, r.glossary = FakeModel(probs), None, "", None
    r.candidates, r._cur = cands, None
    r.lang_file = str(tmp_path / "lang") if tmp_path else None
    return r


AUDIO = np.zeros(32000, dtype=np.float32)  # 2 s: long enough to switch language (MIN_SWITCH_SECS)


def test_pick_candidate_restricts_to_set():
    probs = [("de", 0.6), ("ru", 0.3), ("en", 0.1)]
    assert pick_candidate(probs, ("en", "ru")) == "ru"
    assert pick_candidate(dict(probs), ("en",)) == "en"
    assert pick_candidate([("de", 1.0)], ("en", "ru"), fallback="ru") == "ru"
    assert parse_candidates("en, RU,en") == ("en", "ru")
    assert parse_candidates(None) == ("en", "ru", "ja", "zh")


def test_asr_redetects_every_segment_and_does_not_lock(tmp_path):
    r = make([{"en": 0.7, "ru": 0.2, "zh": 0.1}, {"en": 0.1, "ru": 0.8, "zh": 0.1}], tmp_path)
    assert r.transcribe(AUDIO).lang == "en"
    assert r.transcribe(AUDIO).lang == "ru"
    assert r.model.langs == ["en", "ru"] and r.lang is None
    assert read_lang(r.lang_file) == "ru"


def test_zh_segment_uses_zh_and_keeps_follow_file(tmp_path):
    r = make([{"ru": 0.7, "en": 0.2, "zh": 0.1}, {"zh": 0.9, "en": 0.05, "ru": 0.05}], tmp_path)
    assert r.transcribe(AUDIO).lang == "ru"
    t = r.transcribe(AUDIO)
    assert t.lang == "zh" and r.model.langs[-1] == "zh"
    assert read_lang(r.lang_file) == "ru"  # zh did not overwrite the speak target


def test_partial_reuses_last_language_without_detecting(tmp_path):
    r = make([{"ru": 0.9, "en": 0.1, "zh": 0.0}], tmp_path)
    r.transcribe(AUDIO)
    n = r.model.detects
    assert r.partial(AUDIO).lang == "ru" and r.model.detects == n


def test_explicit_lang_unchanged():
    r = make([], None, cands=None)
    r.lang = "ja"
    r.transcribe(AUDIO)
    assert r.model.langs == ["ja"] and r.model.detects == 0


def test_follow_file_roundtrip(tmp_path):
    f = tmp_path / "s"
    assert read_lang(f) == "en"  # missing -> default
    write_lang(f, "ru")
    assert read_lang(f) == "ru"
    write_lang(f, "zh")
    assert read_lang(f) == "en"  # not a speak language -> default
    write_lang(f, "ru")
    clear_lang(f)
    assert not f.exists()
    assert read_lang(tmp_path / "no" / "dir" / "x", "ru") == "ru"
    write_lang(tmp_path / "no" / "dir" / "x", "ru")  # must not raise


def test_proto_target_follows_file(tmp_path):
    from argparse import Namespace

    from tinglongwu.reverse.proto import FollowTTS, _target

    f = tmp_path / "s"
    a = Namespace(to="follow", follow_file=str(f))
    assert _target(a) == "en"
    write_lang(f, "ru")
    assert _target(a) == "ru"
    assert _target(Namespace(to="en")) == "en"

    made = []

    class C:
        def __init__(self, k):
            self.k, self.speaker = k, 0
            made.append(k)

        def speak(self, t, lang=None):
            return (self.k, lang)

    tts = FollowTTS({"en": lambda: C("en"), "ru": lambda: C("ru")})
    assert tts.speak("hello") == ("en", "en") and made == ["en"]  # ru engine not loaded yet
    assert tts.speak("привет") == ("ru", None) and made == ["en", "ru"]
    tts.speaker = 3
    assert tts.clients["en"].speaker == tts.clients["ru"].speaker == 3


def test_translator_switches_pair_per_segment():
    made = []

    def factory(s, t):
        made.append((s, t))
        return lambda x: f"{s}>{t}:{x}"

    tr = PivotTranslator(None, "tw", step_factory=factory)
    tr.translate("hi", "en", "zh")
    tr.translate("привет", "ru", "zh")
    tr.translate("hi again", "en", "zh")
    assert made == [("en", "zh"), ("ru", "en")]  # each step built once, cached


class ListSink(Sink):
    def __init__(self):
        self.r = []

    def write(self, r: Result):
        self.r.append(r)


def test_pipeline_zh_segment_passthrough_traditional():
    class Src:
        def frames(self):
            return iter(())

        def close(self):
            pass

    calls = []

    class Tr:
        zh_variant = "tw"

        def translate(self, text, src, tgt):
            calls.append(src)
            return Translation(None, "X")

    # drive emit through run() with a fake recognizer yielding zh then en
    class Seger:
        epoch = 1

        def segments(self, frames):
            yield np.ones(16000, dtype=np.float32) * 0.1
            yield np.ones(16000, dtype=np.float32) * 0.1

    from tinglongwu.asr import Recognizer, Transcript

    class Rec(Recognizer):
        def __init__(self):
            self.q = [Transcript("这个软件很好。", "zh"), Transcript("It is good.", "en")]

        def transcribe(self, audio):
            return self.q.pop(0)

    sink = ListSink()
    run(Src(), Seger(), Rec(), Tr(), sink, "zh")
    assert calls == ["en"]  # zh never translated
    assert sink.r[0].src == "zh" and sink.r[0].target == sink.r[0].original == "這個軟體很好。"
    assert sink.r[1].target == "X"


def test_sticky_short_segment_keeps_current():
    probs = {"en": 0.4, "ru": 0.55}
    assert pick_candidate(probs, ("en", "ru"), current="en", seconds=0.8) == "en"  # too short
    assert pick_candidate(probs, ("en", "ru"), current="en", seconds=3.0) == "en"  # margin 0.15 < 0.2


def test_sticky_switches_when_clear_and_long():
    probs = {"en": 0.2, "ru": 0.7}
    assert pick_candidate(probs, ("en", "ru"), current="en", seconds=2.0) == "ru"


def test_sticky_first_segment_unrestricted_and_candidates_only():
    assert pick_candidate({"en": 0.4, "ru": 0.5}, ("en", "ru"), current=None, seconds=0.3) == "ru"
    assert pick_candidate({"en": 0.1, "ru": 0.5, "fr": 0.9}, ("en", "ru"), current="en", seconds=5) == "ru"


def test_recognizer_does_not_flip_on_short_audio():
    m = FakeModel([{"en": 0.4, "ru": 0.6}, {"en": 0.2, "ru": 0.7}])
    r = asr_mod.FasterWhisperRecognizer.__new__(asr_mod.FasterWhisperRecognizer)
    r.candidates, r._cur, r.model, r.lang = ("en", "ru"), "en", m, None
    assert r._lang_for(np.zeros(8000, dtype="float32")) == "en"   # 0.5 s
    assert r._lang_for(np.zeros(48000, dtype="float32")) == "ru"  # 3 s and margin 0.5
