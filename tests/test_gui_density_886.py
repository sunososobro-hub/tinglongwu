"""#886: the Adjust panel fits a 1366x768 screen, with Advanced closed (default) and open."""
import pytest

SCREEN_W, SCREEN_H = 1366, 768
MAX_H = 600  # Acceptance: <= 600 px at 100% scaling (leaves room for the title bar and taskbar on 768 px)


@pytest.fixture
def small_screen_app(tmp_path, monkeypatch):
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
    monkeypatch.setattr(tk.Misc, "winfo_screenheight", lambda self: SCREEN_H)  # a 1366x768 laptop
    (tmp_path / "tinglongwu").mkdir()
    (tmp_path / "tinglongwu" / "gui.json").write_text('{"tour_seen": true}', "utf-8")
    made = []

    def make(lang="en"):
        monkeypatch.setattr(i18n, "_override", lang)
        a = app.App()
        a.toggle_adjust()
        a.root.update()
        made.append(a)
        return a

    yield make
    for a in made:
        a.stop_ev.set()
        try:
            a.root.destroy()
        except tk.TclError:
            pass


def _size(w):
    w.update_idletasks()
    return w.winfo_reqwidth(), w.winfo_reqheight()


def test_log_height_is_short_on_a_768_screen():
    pytest.importorskip("tkinter")
    from tinglongwu.gui import app

    assert app.log_height(768) == app.LOG_H_SHORT < app.LOG_H == app.log_height(1080)


def test_default_panel_is_collapsed_and_small(small_screen_app):
    a = small_screen_app()
    assert a.settings["advanced_open"] is False
    hidden = [a.combos[k] for k in ("quality", "model", "source", "preset", "engine", "target")]
    assert not any(w.winfo_ismapped() for w in hidden)
    _, bar_h = _size(a.root)
    _, panel_h = _size(a.panel)
    assert bar_h + panel_h <= MAX_H


@pytest.mark.parametrize("lang", ["en", "ru"])
def test_advanced_open_fits_a_1366x768_screen(small_screen_app, lang):
    a = small_screen_app(lang)
    a.set_advanced(True)
    a.root.update()
    assert all(a.combos[k].winfo_ismapped() for k in ("quality", "model", "engine"))
    w, h = _size(a.panel)
    assert h <= MAX_H, h  # dev before #886: 877 px, taller than the screen
    assert w <= SCREEN_W, w


def test_settings_sit_in_two_columns(small_screen_app):
    a = small_screen_app()
    a.set_advanced(True)
    a.root.update()
    cols = {int(a.combos[k].grid_info()["column"]) for k in ("quality", "model")}
    assert cols == {1, 5}
    # owner 2026-11-15: the two languages live only at the top (my language / other side's), not under Advanced
    assert not a.combos["lang"].winfo_ismapped() and not a.combos["target"].winfo_ismapped()
    assert a.home_lang.winfo_ismapped() and a.home_target.winfo_ismapped()


def test_empty_send_notice_takes_no_row(small_screen_app):
    """The speak send-mode notice line is only on screen while it has text (confirm mode has none)."""
    a = small_screen_app()
    a.set_advanced(True)
    a.v_send_mode.set("confirm")
    a.root.update()
    assert not a.lbl_send_notice.winfo_ismapped()
    for mode in ("countdown", "auto"):
        a.v_send_mode.set(mode)
        a.root.update()
        assert a.lbl_send_notice.winfo_ismapped() and a.lbl_send_notice.cget("text")
