"""#314 first-launch overlay: 3 numbered steps, "Got it" closes it for good (gui.json), "?" reopens it; text in the
UI language (zh-TW / zh-CN / en, plus ru). GUI tests need a display (CI: Xvfb on ubuntu) and skip without one."""
from __future__ import annotations

import json

import pytest

from tinglongwu.gui import core


@pytest.mark.parametrize("ui,tag,want", [("en", "en-US", "en"), ("zh-TW", "zh-TW", "zh-TW"),
                                         ("zh-TW", "zh-Hans-CN", "zh-CN"), ("zh-TW", "zh_CN.UTF-8", "zh-CN"),
                                         ("en", "zh-CN", "en"), ("ru", "ru-RU", "ru"), ("xx", "", "en")])
def test_tour_language_follows_ui_and_simplified_chinese_system(ui, tag, want):
    assert core.tour_lang(ui, tag) == want


def test_tour_text_has_three_numbered_steps_in_every_language():
    texts = {lang: core.tour_text(ui, tag) for lang, (ui, tag) in
             {"en": ("en", ""), "zh-TW": ("zh-TW", "zh-TW"), "zh-CN": ("zh-TW", "zh-CN"), "ru": ("ru", "")}.items()}
    for tx in texts.values():
        assert [tx[k][:2] for k in ("step1", "step2", "step3")] == ["1.", "2.", "3."]
        assert tx["title"] and tx["ok"]
    assert len({tx["step3"] for tx in texts.values()}) == 4  # really translated, zh-CN differs from zh-TW
    assert texts["en"]["ok"] == "Got it"


def test_tour_seen_is_remembered_in_gui_json(tmp_path):
    p = tmp_path / "gui.json"
    s = core.load_settings(p)
    assert core.tour_on_start(s)
    s["tour_seen"] = True
    core.save_settings(s, p)
    assert not core.tour_on_start(core.load_settings(p))
    p.write_text(json.dumps({"tour_seen": "yes"}), "utf-8")  # wrong type -> default (show again)
    assert core.tour_on_start(core.load_settings(p))


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    from tinglongwu import i18n
    from tinglongwu.gui import app

    monkeypatch.setenv("APPDATA", str(tmp_path))  # gui.json -> tmp_path/tinglongwu/gui.json
    monkeypatch.setattr(i18n, "_system_tag", lambda: "en-US")
    monkeypatch.setattr(i18n.userdirs, "write_config_value", lambda k, v: None)
    monkeypatch.setattr(i18n, "_override", "en")

    def fake_work(self, s, stop):
        stop.wait(10)
        self.q.put(("stopped",))

    monkeypatch.setattr(app.App, "_work", fake_work)
    made = []

    def make():
        a = app.App()
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


def _bar_text(a) -> str:
    a.root.update()
    return a.overlay.text.get("1.0", "end-1c")


def test_first_run_shows_overlay_second_run_does_not_and_question_mark_reopens(make_app, tmp_path):
    a = make_app()
    assert a.tour.visible and a.tour.win.state() == "normal"  # first run: opens by itself
    assert a.tour.step1.cget("text").startswith("1.") and a.tour.btn_ok.cget("text") == "Got it"
    assert str(a.tour.lang_box.cget("textvariable")) == str(a.v_lang)  # step 1 is the real source-language box
    assert a.overlay.visible and _bar_text(a) == "3. Subtitles appear here - drag to move"  # step 3 in the bar

    a.tour.btn_ok.invoke()  # "Got it"
    assert not a.tour.visible and a.tour.win.state() == "withdrawn"
    assert not a.overlay.visible  # nothing running: the demo bar goes away
    saved = json.loads((tmp_path / "tinglongwu" / "gui.json").read_text("utf-8"))
    assert saved["tour_seen"] is True and saved["floating_bar"] is True  # subtitles do appear in the bar later
    a.quit()

    b = make_app()  # second run: no overlay
    assert not b.tour.visible and b.tour.win.state() == "withdrawn" and not b.overlay.visible
    assert not hasattr(b, "btn_help")  # "?" is not on the #455 bar (it made the bar 442 px > 420 px)
    b.btn_tour.invoke()  # "?" in the Adjust panel
    b.root.update()
    assert b.tour.visible and b.tour.win.state() == "normal" and b.overlay.visible


def test_subtitle_bar_menu_reopens_the_overlay(make_app):
    a = make_app()
    a.tour.btn_ok.invoke()
    m = a.overlay.menu
    idx = [i for i in range(m.index("end") + 1) if m.type(i) == "command" and m.entrycget(i, "label") == "? 3-step guide"]
    assert len(idx) == 1
    m.invoke(idx[0])
    a.root.update()
    assert a.tour.visible and a.overlay.visible


def test_tour_asks_the_os_locale_only_once_and_only_for_chinese(make_app, monkeypatch):
    from tinglongwu import i18n

    calls = []
    monkeypatch.setattr(i18n, "_system_tag", lambda: calls.append(1) or "zh-CN")
    a = make_app()
    calls.clear()
    for _ in range(3):
        a.tour.text()
    assert calls == []  # English UI: the overlay never asks
    a.v_ui_lang.set("繁體中文")
    a.switch_lang()  # relabels the overlay: asks once
    calls.clear()
    for _ in range(3):
        a.tour.text()
    assert calls == [] and a.tour.text()["ok"] == "知道了" and a.tour.text()["title"] == "三步开始"  # cached
