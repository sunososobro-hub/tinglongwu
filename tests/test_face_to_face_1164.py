import pytest

from tinglongwu import face_to_face as f2f
from tinglongwu.asr import Transcript
from tinglongwu.gui.core import SameLanguageError


def _dir(mine, partner, text, lang):
    t = Transcript(text, lang)
    return f2f.route(mine, partner, t.lang)


def test_partner_language_goes_to_mine():
    assert _dir("zh-tw", "en", "How are you?", "en")["target"] == "zh-tw"


def test_my_language_goes_to_partner():
    assert _dir("zh-tw", "en", "你好嗎", "zh")["target"] == "en"


def test_zh_variants_count_as_zh():
    assert _dir("zh-tw", "en", "你好", "zh-cn")["target"] == "en"
    assert _dir("en", "zh-cn", "你好", "zh-tw")["target"] == "en"
    assert _dir("zh-cn", "en", "你好", "zh-tw")["target"] == "en"


def test_third_language_falls_back_to_mine():
    r = _dir("zh-tw", "en", "Guten Tag", "de")
    assert r == {"src": "de", "target": "zh-tw"}
    assert f2f.route("zh-tw", "en", None)["target"] == "zh-tw"


def test_candidates_restricted_to_two_languages():
    assert f2f.candidates("zh-tw", "ja") == ("zh", "ja")
    assert f2f.candidates("zh-cn", "en") == ("zh", "en")


def test_same_language_raises():
    with pytest.raises(SameLanguageError):
        f2f.candidates("zh-tw", "zh-cn")
    with pytest.raises(SameLanguageError):
        f2f.route("en", "en", "en")


def test_pipeline_routes_each_utterance_by_detected_language(monkeypatch):
    """#1164: pipeline.run picks the target per utterance via `route` (fake ASR + fake translator, no models)."""
    import numpy as np
    from tinglongwu.asr import Recognizer, Transcript
    from tinglongwu.pipeline import run

    utter = iter([("How are you today my friend", "en"), ("我很好謝謝你今天過得怎麼樣", "zh"), ("Guten Tag meine Freunde", "de")])

    class ASR(Recognizer):
        def transcribe(self, audio):
            return Transcript(*next(utter))

    class Src:
        def frames(self):
            for _ in range(3):
                yield np.full(16000, 0.3, dtype="float32")

        def close(self):
            pass

    class OneSeg:
        def segments(self, frames):
            yield from frames

    class Tr:
        def translate(self, text, src, tgt):
            return f"[{src}>{tgt}]"

    class Sink:
        def __init__(self):
            self.rows = []

        def write(self, r):
            self.rows.append((r.src, r.tgt))

    import tinglongwu.pipeline as pl
    monkeypatch.setattr(pl, "translate_checked", lambda tr, text, src, tgt: type("T", (), {"pivot": None, "text": f"[{src}>{tgt}]"})())
    sink = Sink()
    run(Src(), OneSeg(), ASR(), Tr(), sink, "zh-tw", audio_clock=True,
        route=lambda lang: f2f.route("zh-tw", "en", lang)["target"])
    assert sink.rows == [("en", "zh-tw"), ("zh", "en"), ("de", "zh-tw")]
