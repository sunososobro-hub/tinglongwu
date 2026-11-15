"""#1020: the GUI home says what it does: a Freeze subtitles toggle by the subtitle box, status lines in the UI
language, the 聽攏有 name, and a language change on the home card restarts the running session by itself."""
from __future__ import annotations

import pytest

from tinglongwu import i18n
from tinglongwu.gui.core import load_status_text


@pytest.fixture
def ui(monkeypatch):
    def use(lang):
        monkeypatch.setattr(i18n, "_override", lang)
    return use


def test_loaded_line_in_zh_tw(ui):
    ui("zh-TW")
    msg = "listening - loaded in 4.9 s (speech model 3.5 s, translation engine 0.1 s, voice 1.2 s)"
    assert load_status_text(msg) == "聆聽中－載入 4.9 秒（語音模型 3.5 秒、翻譯 0.1 秒、語音 1.2 秒）"
    assert load_status_text("loading speech model... (0.0 s)") == "載入語音模型中…（0.0 秒）"
    assert load_status_text("listening") == "聆聽中"
    assert load_status_text("listening - loaded in 2.0 s") == "聆聽中－載入 2.0 秒"


def test_loaded_line_in_en_and_unknown_text_unchanged(ui):
    ui("en")
    assert load_status_text("listening - loaded in 4.9 s (speech model 3.5 s, translation engine 0.1 s)") == \
        "Listening - loaded in 4.9 s (speech model 3.5 s, translation 0.1 s)"
    assert load_status_text("loading speaker model... (1.0 s)") == "Loading speaker model... (1.0 s)"
    assert load_status_text("something else") == "something else"


def test_pause_labels_say_freeze_subtitles(ui):
    ui("zh-TW")
    assert (i18n.t("gui_pause"), i18n.t("gui_resume")) == ("凍結字幕", "繼續更新字幕")
    ui("en")
    assert (i18n.t("gui_pause"), i18n.t("gui_resume")) == ("Freeze subtitles", "Resume subtitles")


class _Alive:
    def is_alive(self):
        return True


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    from tinglongwu.gui import app

    monkeypatch.setenv("APPDATA", str(tmp_path))
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


def test_freeze_button_sits_with_the_subtitle_controls(make_app):
    a = make_app()
    bar = a.btn_pause.master
    labels = [w.cget("text") for w in bar.winfo_children() if str(w.winfo_class()) == "TButton"]
    assert "A+" in labels and "A-" in labels  # same row as A- / A+ and the floating-bar toggle
    assert a.btn_pause.cget("text") == "Freeze subtitles"
    a.toggle_pause()
    assert a.btn_pause.cget("text") == "Resume subtitles"


def test_sidebar_title_is_tinglongwu_name(make_app):
    a = make_app()
    texts = [a.side.itemcget(i, "text") for i in a.side.find_all() if a.side.type(i) == "text"]
    assert "聽攏有" in texts and "聽龍" not in texts


def test_home_language_change_restarts_running_session_after_debounce(make_app, monkeypatch):
    from tinglongwu.gui import app as app_mod

    a = make_app()
    a.worker = _Alive()
    a.quick_mode = app_mod.BOTH_MODE
    a.run_snapshot, a.run_pair = a._start_values(), a._home_pair()
    restarts = []
    monkeypatch.setattr(a, "_restart_now", lambda: restarts.append(a.quick_mode))
    values = [v for v in a.home_target.cget("values") if v != a.home_target.get()]
    a.home_target.set(values[0])
    a.home_target.set(values[1])  # a second change within the debounce: still one restart
    a.root.update()
    assert restarts == []
    assert not a.lbl_restart.winfo_manager()  # no "restart to apply" notice for the home-card pair
    a._pair_restart_fire()  # the 0.8 s timer
    assert restarts == [app_mod.BOTH_MODE]


def test_home_language_change_waits_while_loading_and_not_when_stopped(make_app, monkeypatch):
    a = make_app()
    restarts = []
    monkeypatch.setattr(a, "_restart_now", lambda: restarts.append(1))
    a._pair_restart_fire()  # not running: nothing
    assert restarts == []
    a.worker = _Alive()
    a.run_snapshot, a.run_pair = a._start_values(), ("xx", "yy")
    a.load_t0 = 1.0  # still loading: try again later
    a._pair_restart_fire()
    assert restarts == [] and a._pair_after is not None
    a.load_t0 = None
    a._pair_restart_fire()
    assert restarts == [1]


def test_other_start_time_setting_still_shows_the_notice(make_app):
    a = make_app()
    a.v_send_mode.set("auto")
    a.worker = _Alive()
    a.run_snapshot, a.run_pair = a._start_values(), a._home_pair()
    a.v_send_mode.set("countdown")
    assert a.lbl_restart.winfo_manager()
