"""0.3.8: the 情境 row: meeting = listen + speak, video = listen only (no mic leg, no voice output)."""
import pytest

from tinglongwu.gui import core
from tinglongwu.i18n import MESSAGES

S = dict(core.DEFAULTS, target="zh-tw", lang="en", voice=True)


def test_meeting_scene_is_two_way():
    mode, listen, speak = core.scene_runs(dict(S, scene="meeting"))
    assert mode == core.BOTH_MODE and speak is not None and speak["source"] == "mic"
    assert listen["source"] == "system"


def test_video_scene_listens_only():
    mode, run, speak = core.scene_runs(dict(S, scene="video"))
    assert mode == core.WATCH_MODE and speak is None  # the mic / speak pipeline is never built
    assert run["source"] == "system" and run["voice"] is False  # nothing spoken to any output (CABLE)
    assert S["voice"] is True  # the saved settings are untouched


@pytest.mark.parametrize("scene,want", [("meeting", core.BOTH_MODE), ("video", core.WATCH_MODE),
                                        ("", core.WATCH_MODE), ("bogus", core.WATCH_MODE), (None, core.WATCH_MODE)])
def test_scene_mode(scene, want):
    assert core.scene_mode({"scene": scene}) == want


def test_video_scene_same_language_still_raises():
    with pytest.raises(core.SameLanguageError):
        core.scene_runs(dict(S, scene="video", lang="zh", target="zh-tw"))


def test_start_button_stops_in_watch_mode():
    assert core.start_button("running", core.WATCH_MODE) == ("gui_stop", True)
    assert core.start_button("idle", None) == ("gui_quick_both", True)


def test_scene_persisted(tmp_path):
    p = tmp_path / "gui.json"
    assert core.save_settings(dict(core.DEFAULTS, scene="video"), p)
    assert core.load_settings(p)["scene"] == "video"
    assert core.load_settings(tmp_path / "none.json")["scene"] == "video"


def test_scene_texts_in_every_language():
    for lang, m in MESSAGES.items():
        for k in ("gui_scene", "gui_scene_meeting", "gui_scene_video"):
            assert m.get(k), (lang, k)


def test_fresh_install_defaults_to_listen_only_1343(tmp_path):
    s = core.load_settings(tmp_path / "none.json")
    assert core.scene_of(s) == "video" and core.scene_mode(s) == core.WATCH_MODE


def test_saved_meeting_scene_kept_1343(tmp_path):
    p = tmp_path / "gui.json"
    assert core.save_settings(dict(core.DEFAULTS, scene="meeting"), p)
    assert core.scene_of(core.load_settings(p)) == "meeting"
