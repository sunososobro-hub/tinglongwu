"""#85 owner check, scripted: in the real GUI set lines 1 / 5 and font 14 / 40 -> the subtitle bar changes at once
(no restart), close the app, start it again -> the values are still there. Needs a display (CI: Xvfb on ubuntu);
skips without one."""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    from tinglongwu import i18n
    from tinglongwu.gui import app

    monkeypatch.setenv("APPDATA", str(tmp_path))  # gui.json goes to tmp_path/tinglongwu/gui.json
    monkeypatch.setattr(i18n, "_system_tag", lambda: "en-US")
    monkeypatch.setattr(i18n.userdirs, "write_config_value", lambda k, v: None)
    monkeypatch.setattr(i18n, "_override", "en")
    made = []

    def make():
        a = app.App()
        a.root.update()
        made.append(a)
        return a

    yield make
    for a in made:
        try:
            a.root.destroy()
        except tk.TclError:
            pass


def _bar_rows_px(a) -> int:
    a.root.update()
    return int(a.overlay.box.cget("height"))


def test_lines_and_font_apply_live_and_survive_restart(make_app, tmp_path):
    a = make_app()
    a.overlay.show()
    a.v_lines.set("1")
    a.v_font.set("14")
    h1 = _bar_rows_px(a)
    assert a.overlay.rows == 1 and a.overlay.font.cget("size") == 14

    a.v_lines.set("5")  # typed into the spinbox: applies without a restart
    h5 = _bar_rows_px(a)
    assert a.overlay.rows == 5 and h5 > h1 * 3
    a.v_font.set("40")
    h40 = _bar_rows_px(a)
    assert a.overlay.font.cget("size") == 40 and h40 > h5
    assert a.overlay.text.tag_cget("final", "font")  # the shown lines use the new font

    a.quit()  # the window's close button
    saved = json.loads((tmp_path / "tinglongwu" / "gui.json").read_text("utf-8"))
    assert saved["overlay_lines"] == 5 and saved["font_size"] == 40

    b = make_app()  # restart
    assert b.v_lines.get() == "5" and b.v_font.get() == "40"
    assert b.overlay.rows == 5 and b.overlay.font.cget("size") == 40


def test_out_of_range_input_is_ignored_live(make_app):
    a = make_app()
    a.v_lines.set("3")
    a.v_lines.set("99")  # out of range: ignored, the bar keeps 3 rows
    a.v_font.set("abc")
    a.root.update()
    assert a.overlay.rows == 3 and a.settings["overlay_lines"] == 3
