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
    assert "hymt2-1.8b" in [k for p in pod.needed_packs("en", code) for k in pod.PACKS[pod.real_pack(p)]]


def test_same_language_still_rejected():
    with pytest.raises(core.SameLanguageError):
        core.session_route("listen", "ja", "ja")


@pytest.mark.parametrize("code", ["es", "fr", "de", "th", "vi", "id"])
@pytest.mark.parametrize("ui", ["en", "ru", "zh-TW"])
def test_hymt2_target_dialog_names_translator_not_ko(code, ui, tmp_path):  # #1175
    pack, mb = pod.first_missing("en", code, tmp_path)
    text = pod.dialog_text(ui, pack, mb)
    assert "hymt2" in text and "ko" not in text.replace("hymt2", "") and "韓" not in text and "корей" not in text
    assert mb == 1100 and pod.PACKS[pod.real_pack(pack)] == ["hymt2-1.8b"] and pod.real_pack(pack) == "ko"  # the same model is still downloaded


@pytest.mark.parametrize("code,pack", [("ja", "ja"), ("ko", "ko"), ("ru", "ru")])
def test_native_targets_keep_their_pack_name(code, pack, tmp_path):  # #1175
    assert pod.first_missing("en", code, tmp_path)[0] == pack
