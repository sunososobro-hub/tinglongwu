"""#294 easy start: first-screen "Listen to others" / "I speak" buttons, my-language only, Loading -> Ready."""
import pytest

from tinglongwu.gui import core


def test_listen_uses_pc_sound_and_my_language_without_touching_saved_settings():
    saved = dict(core.DEFAULTS, target="zh-cn", source="mic", lang="auto")
    run = core.quick_settings(saved, "listen")
    assert (run["source"], run["lang"], run["target"]) == ("system", "auto", "zh-cn")
    assert saved["source"] == "mic"  # the saved choice is not overwritten
    assert core.settings_to_argv(run)[:6] == ["--lang", "auto", "--to", "zh", "--zh-variant", "cn"]
    assert "--mic" not in core.settings_to_argv(run)


@pytest.mark.parametrize("mine,lang,target", [("zh-tw", "zh", "en"), ("zh-cn", "zh", "en"), ("en", "en", "zh-tw"),
                                              ("bogus", "zh", "en")])
def test_speak_uses_mic_from_my_language_to_the_other(mine, lang, target):
    run = core.quick_settings(dict(core.DEFAULTS, target=mine), "speak")
    assert (run["source"], run["lang"], run["target"]) == ("mic", lang, target)
    assert "--mic" in core.settings_to_argv(run)


def test_unknown_quick_mode_is_rejected():
    with pytest.raises(ValueError):
        core.quick_settings(dict(core.DEFAULTS), "sing")


def test_quick_button_states():
    assert core.quick_buttons("idle") == {"listen": ("gui_quick_listen", True), "speak": ("gui_quick_speak", True)}
    assert core.quick_buttons("loading", "listen") == {"listen": ("gui_loading", False), "speak": ("gui_loading", False)}
    assert core.quick_buttons("running", "speak") == {"listen": ("gui_quick_listen", False), "speak": ("gui_stop", True)}
    assert core.quick_buttons("busy", "speak") == {"listen": ("gui_quick_listen", False),
                                                   "speak": ("gui_quick_speak", False)}
