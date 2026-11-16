"""#1163: My language / Translate to offers ja, ko, es, fr, de, th, vi, id end to end."""
import pytest

from tinglongwu import hymt
from tinglongwu.gui import core, pack_on_demand as pod

NEW = ["ja", "ko", "es", "fr", "de", "th", "vi", "id"]


@pytest.mark.parametrize("code", NEW)
def test_target_routes(code):
    assert code in core.TARGETS
    (leg,) = core.session_route("listen", code, "en")
    assert leg["target"] == code
    (leg,) = core.session_route("speak", "en", code)
    assert leg["target"] == code
    assert core.quick_settings({"target": code, "lang": "en"}, "listen")["target"] == code
    listen, speak = core.both_settings({"target": code, "lang": "en"})
    assert listen["target"] == code and speak["lang"] == code


@pytest.mark.parametrize("code", NEW)
def test_prompt_and_labels(code):
    assert code not in hymt.build_prompt("hello", "en", code).split("into ")[1].split(".")[0]
    for ui in ("en", "ru", "zh-TW", "ja"):
        assert core.choice_label("target", code, ui) != code
    assert core.choice_value("target", core.choice_label("target", code, "en"), core.TARGETS) == code


@pytest.mark.parametrize("code", NEW)
def test_packs(code):
    assert "hymt2-1.8b" in [k for p in pod.needed_packs("en", code) for k in pod.PACKS[p]]


def test_same_language_still_rejected():
    with pytest.raises(core.SameLanguageError):
        core.session_route("listen", "ja", "ja")
