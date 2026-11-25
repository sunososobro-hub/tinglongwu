"""#1603: one top toolbar holds the language pair, swap, situation, offline badge, A- / A+ and settings."""
import pytest


@pytest.fixture
def gui(tmp_path, monkeypatch):
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
    (tmp_path / "tinglongwu").mkdir()
    (tmp_path / "tinglongwu" / "gui.json").write_text('{"tour_seen": true}', "utf-8")
    a = app.App()
    a.root.geometry("460x640")  # the default main window; a clipped widget would be unmapped
    a.root.update()
    yield a
    a.stop_ev.set()
    try:
        a.root.destroy()
    except tk.TclError:
        pass


def test_toolbar_widgets_share_one_parent_row(gui):
    a = gui
    widgets = [a.btn_pair, a.btn_swap, a.scene_combo, a.lbl_offline, a.btn_font_down, a.btn_font_up, a.btn_settings]
    assert all(w.master is a.toolbar for w in widgets)
    assert all(w.winfo_ismapped() for w in widgets)
    assert len({w.winfo_rooty() for w in widgets}) <= 2  # one row (a few px of centring at most)
    assert a.btn_adjust is a.btn_settings
    assert a.toolbar.winfo_reqwidth() <= 560  # same bound as the bottom bar (MAIN_BAR_MAX_W); CI fonts run ~20% wider than 460 px in the widest UI language
    assert not a.home_lang.winfo_ismapped() and not a.home_target.winfo_ismapped()


def test_swap_exchanges_the_two_languages(gui):
    from tinglongwu.langs import from_label

    a = gui
    a.v_lang.set(a.home_lang.cget("values")[[from_label(x) for x in a.home_lang.cget("values")].index("ja")])
    a.v_target.set("en")
    a.root.update()
    before = a.btn_pair.cget("text")
    assert a.btn_swap.cget("text") and a.btn_swap.accessible_name == "swap languages"
    a.btn_swap.invoke()
    a.root.update()
    assert from_label(a.v_lang.get()) == "en" and a.v_target.get() == "ja"
    assert a.btn_pair.cget("text") != before and "→" in a.btn_pair.cget("text")


def test_badge_says_offline_and_carries_other_statuses(gui):
    a = gui
    assert "Offline" in a.lbl_offline.cget("text") and not a.lbl_status.winfo_ismapped()
    a._status_text("Loading speech model, please wait")
    a.root.update()
    assert "Loadin" in a.lbl_offline.cget("text") and a.lbl_status.winfo_ismapped()
    a.set_status("gui_ready")
    a.root.update()
    assert "Offline" in a.lbl_offline.cget("text") and not a.lbl_status.winfo_ismapped()
