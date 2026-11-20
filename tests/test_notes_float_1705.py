"""#1705: the meeting notes can pop out into a floating window; its place / size and state persist (headless)."""
import json

from tinglongwu.gui.topics_panel import (NOTES_DEFAULT_H, NOTES_DEFAULT_W, NOTES_MIN_H, NOTES_MIN_W, TopicPanel,
                                         notes_float_on, notes_geometry, remember_notes_geometry)
from tinglongwu.settings import DEFAULTS, load_settings
from tinglongwu.topics import Topic

SW, SH = 1920, 1080


def test_defaults_are_docked_with_no_saved_place():
    assert DEFAULTS["notes_float"] is False
    assert all(DEFAULTS[k] is None for k in ("notes_x", "notes_y", "notes_w", "notes_h"))
    assert notes_float_on(DEFAULTS) is False
    assert notes_float_on({"notes_float": True}) is True
    assert notes_float_on({"notes_float": 1}) is False


def test_geometry_default_is_right_edge():
    w, h = NOTES_DEFAULT_W, NOTES_DEFAULT_H
    assert notes_geometry({}, SW, SH).startswith(f"{w}x{h}+{SW - w - 40}+")


def test_geometry_uses_saved_place_and_clamps_size():
    s = {"notes_x": 100, "notes_y": 50, "notes_w": 500, "notes_h": 600}
    assert notes_geometry(s, SW, SH) == "500x600+100+50"
    tiny = {"notes_x": 10, "notes_y": 10, "notes_w": 5, "notes_h": 5}
    assert notes_geometry(tiny, SW, SH) == f"{NOTES_MIN_W}x{NOTES_MIN_H}+10+10"
    huge = {"notes_x": 0, "notes_y": 0, "notes_w": 99999, "notes_h": 99999}
    assert notes_geometry(huge, SW, SH) == f"{SW}x{SH}+0+0"


def test_geometry_offscreen_place_falls_back():
    s = {"notes_x": 5000, "notes_y": 20, "notes_w": 400, "notes_h": 400}  # monitor was unplugged
    assert notes_geometry(s, SW, SH) == f"400x400+{SW - 400 - 40}+80"
    assert "+True" not in notes_geometry({"notes_x": True, "notes_y": True}, SW, SH)


def test_remember_ignores_unmapped_window_and_reports_change():
    s = {}
    assert remember_notes_geometry(s, 1, 1, 0, 0) is False and "notes_w" not in s
    assert remember_notes_geometry(s, 400, 500, 30, 40) is True
    assert (s["notes_w"], s["notes_h"], s["notes_x"], s["notes_y"]) == (400, 500, 30, 40)
    assert remember_notes_geometry(s, 400, 500, 30, 40) is False


def test_state_and_place_persist_through_gui_json(tmp_path):
    s = dict(DEFAULTS, notes_float=True)
    remember_notes_geometry(s, 420, 480, 70, 90)
    path = tmp_path / "gui.json"
    path.write_text(json.dumps(s), "utf-8")
    got = load_settings(path, gpu=lambda: False)
    assert got["notes_float"] is True
    assert (got["notes_w"], got["notes_h"], got["notes_x"], got["notes_y"]) == (420, 480, 70, 90)
    path.write_text(json.dumps({"notes_float": "yes", "notes_x": "left", "notes_w": True}), "utf-8")
    bad = load_settings(path, gpu=lambda: False)
    assert bad["notes_float"] is False and bad["notes_x"] is None and bad["notes_w"] is None


def test_floating_window_reads_the_same_model():
    """The floating window redraws from TopicPanel.entries() on on_change, like the side panel: one data path."""
    changes = []
    panel = TopicPanel(on_change=lambda: changes.append(len(panel.entries())))
    panel.add(Topic("A", ["p"], [("me", "x")], 0, 65))
    panel.state(True)
    panel.add(Topic("B", ["q"], [], 65, 130))
    assert changes == [1, 1, 2]
    assert [e["title"] for e in panel.entries()] == ["A", "B"]


def test_every_ui_language_has_the_new_lines():
    from tinglongwu.i18n import MESSAGES

    for lang, msgs in MESSAGES.items():
        for k in ("gui_topics_popout", "gui_topics_dock", "gui_topics_float"):
            assert msgs.get(k), (lang, k)
