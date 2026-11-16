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
