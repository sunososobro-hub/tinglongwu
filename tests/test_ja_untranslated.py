"""#86: Japanese shown untranslated in zh output (ja detected as zh -> only s2t applied; MT echoing the source)."""
import numpy as np
import pytest

from tinglongwu import asr as asr_mod
from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.langfollow import has_kana, script_lang
from tinglongwu.models import ModelNotFound
from tinglongwu.pipeline import run
from tinglongwu.sink import Sink
from tinglongwu.translate import Translation, Translator, translate_checked

OWNER_LINES = ["今月13日から来月16日まではできません", "東京都はウイルスがうつる心配はまだ"]  # issue #86
ZH = {OWNER_LINES[0]: "这个月13日到下个月16日不行", OWNER_LINES[1]: "东京都还担心病毒传染"}


class Src:
    def __init__(self, n):
        self.n = n

    def frames(self):
        for _ in range(self.n):
            yield np.full(16000, 0.1, np.float32)

    def close(self):
        pass


class OneSegPerFrame:
    def segments(self, frames):
        yield from frames


class ScriptedASR(Recognizer):
    def __init__(self, lines):
        self.lines = list(lines)

    def transcribe(self, audio):
        return Transcript(*self.lines.pop(0)) if self.lines else None


class Collect(Sink):
    def __init__(self):
        self.got = []

    def write(self, r):
        self.got.append(r)


class FakeMT(Translator):
    zh_variant = "tw"

    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def translate(self, text, src, tgt):
        self.calls.append((text, src, tgt))
        return Translation(None, self.fn(text, len(self.calls)))


def go(lines, mt):
    sink = Collect()
    run(Src(len(lines)), OneSegPerFrame(), ScriptedASR(lines), mt, sink, tgt="zh")
    return sink.got


@pytest.fixture(autouse=True)
def _en_ui(monkeypatch):
    monkeypatch.setenv("TLWU_UI_LANG", "en")


def test_script_check_overrides_zh_label():
    for line in OWNER_LINES:
        assert script_lang(line, "zh") == "ja"
    assert script_lang("緊急事態宣言的期間是4月16日。", "zh") == "zh"  # real Chinese stays Chinese
    assert script_lang("ウイルス", "ja") == "ja" and script_lang("hello", "en") == "en"


def test_japanese_labelled_zh_is_translated_not_passed_through():
    mt = FakeMT(lambda text, n: ZH[text])
    got = go([(line, "zh") for line in OWNER_LINES], mt)
    assert [c[1:] for c in mt.calls] == [("ja", "zh")] * 2  # MT ran with ja as the source
    assert [r.target for r in got] == [ZH[x] for x in OWNER_LINES]
    assert all(r.src == "ja" and not has_kana(r.target) for r in got)


def test_real_chinese_still_passes_through_without_mt():
    mt = FakeMT(lambda text, n: "X")
    got = go([("今天天气很好", "zh")], mt)
    assert mt.calls == [] and got[0].target == "今天天氣很好"


def test_engine_echo_is_retried_then_marked():
    line = "5月6日までの期間となっています。"  # owner retest: came out unchanged
    mt = FakeMT(lambda text, n: text if n == 1 else "期間到5月6日為止。")
    got = go([(line, "ja")], mt)
    assert len(mt.calls) == 2 and got[0].target == "期間到5月6日為止。"

    stuck = FakeMT(lambda text, n: "這棟建築物を")  # half-translated, kana left
    got = go([(line, "ja")], stuck)
    assert len(stuck.calls) == 2
    assert got[0].target == "[not translated]" and got[0].original == line and not has_kana(got[0].target)


def test_draft_with_kana_is_dropped_without_retry():
    mt = FakeMT(lambda text, n: "這棟建築物を")
    assert translate_checked(mt, "この建物を", "ja", "zh", draft=True) is None and len(mt.calls) == 1
    ok = FakeMT(lambda text, n: "ビル")  # Japanese target: kana is the point
    assert translate_checked(ok, "建築物", "zh", "ja").text == "ビル" and len(ok.calls) == 1


def test_missing_pair_shows_notice_and_keeps_running(capsys):
    def fn(text, n):
        if has_kana(text):
            raise ModelNotFound("Hy-MT2 translation model (Japanese) not found")
        return "你好"

    mt = FakeMT(fn)
    got = go([(OWNER_LINES[0], "zh"), ("hello", "en"), (OWNER_LINES[1], "zh")], mt)
    assert [r.target for r in got] == ["[no ja -> zh translation model installed: run tlwu-setup]", "你好",
                                       "[no ja -> zh translation model installed: run tlwu-setup]"]
    assert capsys.readouterr().err.count("Hy-MT2 translation model (Japanese) not found") == 1  # reported once


# -- recognizer: the language it reports / locks ------------------------------------------------------------

class Seg:
    def __init__(self, t):
        self.text, self.no_speech_prob, self.avg_logprob = t, 0.0, -0.1


class FakeModel:
    def __init__(self, texts, detected="zh"):
        self.texts, self.detected, self.langs = list(texts), detected, []

    def detect_language(self, audio):
        return self.detected, 0.9, [(self.detected, 0.9)]

    def transcribe(self, audio, language=None, **kw):
        self.langs.append(language)
        return [Seg(self.texts.pop(0))], type("I", (), {"language": language or self.detected})()


def rec(texts, lang="auto", cands=None):
    r = asr_mod.FasterWhisperRecognizer.__new__(asr_mod.FasterWhisperRecognizer)
    r.model, r._last, r.glossary = FakeModel(texts), "", None
    r.lang, r.auto = (None if lang == "auto" else lang), lang == "auto"
    r.candidates, r._cur, r.lang_file = cands, None, None
    return r


AUDIO = np.zeros(16000, dtype=np.float32)


def test_auto_does_not_lock_onto_zh_for_japanese():
    r = rec(OWNER_LINES)
    assert r.transcribe(AUDIO).lang == "ja" and r.lang == "ja"
    r.transcribe(AUDIO)
    assert r.model.langs == [None, "ja"]  # the next segment is decoded as Japanese


def test_auto_locked_on_zh_by_kanji_only_line_recovers():
    r = rec(["緊急事態宣言", OWNER_LINES[0], OWNER_LINES[1]])
    assert r.transcribe(AUDIO).lang == "zh"  # no kana: cannot tell yet
    assert r.transcribe(AUDIO).lang == "ja" and r.lang == "ja"
    r.transcribe(AUDIO)
    assert r.model.langs[-1] == "ja"


def test_fixed_zh_and_candidates_label_the_line_ja():
    r = rec([OWNER_LINES[0], "今天天气很好"], lang="zh")
    assert r.transcribe(AUDIO).lang == "ja"
    assert r.transcribe(AUDIO).lang == "zh" and r.lang == "zh"  # the user's explicit choice is kept
    c = rec([OWNER_LINES[0], OWNER_LINES[1]], cands=("en", "zh"))
    assert c.transcribe(AUDIO).lang == "ja" and c.partial(AUDIO).lang == "ja"
