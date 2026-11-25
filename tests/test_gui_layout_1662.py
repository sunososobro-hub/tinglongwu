"""#1662: the main window has no band that holds no control or text (a dark empty row)."""
import pytest

CONTROLS = ("btn", "btn_pause", "btn_mic", "btn_settings", "btn_font_up", "btn_font_down", "scene_combo",
            "lbl_offline", "log_text", "lbl_cpu", "dot")


@pytest.fixture
def app_(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    from tinglongwu import i18n
    from tinglongwu.gui import app

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(i18n, "_system_tag", lambda: "en-US")
    monkeypatch.setattr(i18n.userdirs, "write_config_value", lambda k, v: None)
    monkeypatch.setattr(i18n, "_override", "en")
    monkeypatch.setattr(app.App, "_work", lambda self, s, stop: stop.wait(10))
    (tmp_path / "tinglongwu").mkdir()
    (tmp_path / "tinglongwu" / "gui.json").write_text('{"tour_seen": true}', "utf-8")
    a = app.App()
    a.root.update()
    yield a
    a.stop_ev.set()
    a.root.destroy()


def _has_content(w):
    """True when the widget (or a mapped descendant) is a control or shows text."""
    if not w.winfo_ismapped() or w.winfo_height() <= 1:
        return False
    kids = w.winfo_children()
    cls = w.winfo_class()
    if cls in ("TFrame", "Frame", "Labelframe", "TLabelframe"):
        return any(_has_content(k) for k in kids)
    if cls in ("TLabel", "Label"):
        return bool(str(w.cget("text")).strip())
    return True  # buttons, comboboxes, checkbuttons, text, canvas...


def test_gui_layout_no_empty_band_at_default_size(app_):
    a = app_
    a.root.update()
    rows = [w for w in a.root.winfo_children() if w.winfo_ismapped() and w.winfo_height() > 4]
    assert rows
    empty = [(w.winfo_class(), w.winfo_y(), w.winfo_height()) for w in rows if not _has_content(w)]
    assert empty == []


def test_gui_layout_notice_row_appears_only_with_text(app_):
    a = app_
    assert not a.lbl_notice.winfo_manager()
    a.v_notice.set("something to tell")
    a.root.update()
    assert a.lbl_notice.winfo_ismapped()
    a.v_notice.set("")
    a.root.update()
    assert not a.lbl_notice.winfo_manager()


def test_gui_layout_every_control_still_present(app_):
    for name in CONTROLS:
        assert getattr(app_, name, None) is not None, name
