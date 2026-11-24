"""#1604: the situation alone decides the capture source; the mic toggle shows only in two-way (meeting)."""
import pytest

from tinglongwu.gui.core import mic_toggle_visible, scene_runs
from tinglongwu.i18n import MESSAGES


def test_mic_toggle_only_in_two_way():
    assert mic_toggle_visible({"scene": "meeting"}) is True
    assert mic_toggle_visible({"scene": "video"}) is False
    assert mic_toggle_visible({}) is False  # unknown scene falls back to video


def test_listen_only_capture_source_follows_saved_source_and_old_settings_load():
    mode, listen, speak = scene_runs({"scene": "video", "source": "mic"})
    assert speak is None and listen["source"] == "mic"
    mode, listen, speak = scene_runs({"scene": "video"})
    assert speak is None and listen["source"] != "mic"


def test_status_dot_labels_in_every_language():
    for lang in MESSAGES:
        for k in ("idle", "loading", "on", "error"):
            assert MESSAGES[lang]["gui_dot_" + k], (lang, k)


@pytest.fixture
def app(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    from tinglongwu import i18n
    from tinglongwu.gui import app as appmod

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(i18n, "_system_tag", lambda: "en-US")
    monkeypatch.setattr(i18n.userdirs, "write_config_value", lambda k, v: None)
    monkeypatch.setattr(i18n, "_override", "en")
    (tmp_path / "tinglongwu").mkdir()
    (tmp_path / "tinglongwu" / "gui.json").write_text('{"tour_seen": true}', "utf-8")
    a = appmod.App()
    yield a
    a.root.destroy()


def test_gui_hides_mic_toggle_and_source_button_for_video_shows_mic_for_meeting(app):
    for scene, mic in (("video", False), ("meeting", True), ("video", False)):
        app.settings["scene"] = scene
        app._source_sync()
        app.root.update_idletasks()
        assert bool(app.btn_mic.winfo_manager()) is mic
        assert app.source_row.winfo_manager() == ""
