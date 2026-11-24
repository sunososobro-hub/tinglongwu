"""#1129: owner hand-tests moved to CI. Opens the real GUI (Tk under xvfb) and proves each item's visible behaviour.

Set TLWU_GUI_SHOTS=<dir> to save one PNG per item (needs PIL + a display); without it no image is written.
#1002 (wizard language page) lives in tests/test_installer_language_1002.py (Inno Pascal cannot run on Linux)."""
import os
import urllib.parse

import json

import pytest

SCREEN_W, SCREEN_H = 1366, 768


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    from tinglongwu import i18n
    from tinglongwu.gui import app, core

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(i18n, "_system_tag", lambda: "en-US")
    monkeypatch.setattr(i18n.userdirs, "write_config_value", lambda k, v: None)
    monkeypatch.setattr(i18n, "_override", "en")
    monkeypatch.setattr(tk.Misc, "winfo_screenheight", lambda self: SCREEN_H)
    monkeypatch.setattr(tk.Misc, "winfo_screenwidth", lambda self: SCREEN_W)
    monkeypatch.setattr(core, "_gpu_usable", lambda: False)
    (tmp_path / "tinglongwu").mkdir()
    made = []

    def make(**saved):
        import json

        cfg = {"tour_seen": True, **saved}
        (tmp_path / "tinglongwu" / "gui.json").write_text(json.dumps(cfg), "utf-8")
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


def _shot(app, name):
    out = os.environ.get("TLWU_GUI_SHOTS")
    if not out:
        return
    from PIL import ImageGrab

    os.makedirs(out, exist_ok=True)
    app.root.update()
    ImageGrab.grab(xdisplay=None).save(os.path.join(out, name + ".png"))


def _inside(app, w, win):
    return (w.winfo_rootx() >= win.winfo_rootx() and w.winfo_rooty() >= win.winfo_rooty()
            and w.winfo_rootx() + w.winfo_width() <= win.winfo_rootx() + win.winfo_width()
            and w.winfo_rooty() + w.winfo_height() <= win.winfo_rooty() + win.winfo_height())


@pytest.mark.parametrize("scene", ["meeting", "video"])
def test_982_every_mode_has_two_language_rows_and_no_advanced_ones(make_app, scene):
    a = make_app(scene=scene, advanced_open=True)
    assert a.home_target.winfo_ismapped() and a.home_lang.winfo_ismapped()  # My language / Other side language
    assert not a.combos["target"].winfo_ismapped() and not a.combos["lang"].winfo_ismapped()  # no Advanced rows
    _shot(a, f"982_{scene}")


def test_1001_single_start_control_no_mode_choice(make_app):
    a = make_app()
    assert a.btn is a.both_btn and a.btn.winfo_ismapped()
    assert a.btn.cget("text") == "Start"
    assert not hasattr(a, "listen_btn") and not hasattr(a, "speak_btn")
    _shot(a, "1001_start")


def test_886_main_window_fits_1366x768_with_advanced_collapsed(make_app):
    a = make_app()
    a.root.update_idletasks()
    assert a.settings["advanced_open"] is False
    assert a.root.winfo_reqwidth() <= SCREEN_W and a.root.winfo_reqheight() <= SCREEN_H
    win = a.root
    for w in (a.btn, a.home_target, a.home_lang):
        assert _inside(a, w, win), w
    _shot(a, "886_main")


def test_611_topic_panel_default_follows_gpu(make_app, tmp_path):
    from tinglongwu.gui import core

    model = tmp_path / "m.gguf"
    model.write_bytes(b"x")
    s = {"topics_model": str(model), "topics": None}
    assert core.topics_on(s, has_cuda=True) is True  # a GPU is reported -> on
    assert core.topics_on(s, has_cuda=False) is False  # CPU only -> off
    a = make_app()
    a.render_topics(show=True)
    a.root.update()
    assert a.topics_win.winfo_exists() and a.topics_win.winfo_viewable()
    assert a.topics_text.winfo_ismapped()
    _shot(a, "611_topics")


def test_1379_meeting_notes_switch_on_shows_topics_and_language(make_app, tmp_path):
    from tinglongwu import topics

    model = tmp_path / "m.gguf"
    model.write_bytes(b"x")
    a = make_app()
    a.settings.update(topics=True, topics_model=str(model), target="zh-tw")
    a._sync_topics_var()
    assert a.v_topics.get() is True  # the Settings menu switch and the panel share it
    a.topic_panel.add(topics.Topic("Q3 budget", ["Cut travel by 10%", "Launch date stays 1 Dec"],
                                   [("me", "send the revised budget")], 0, 312))
    a.poll()
    a.root.update()
    assert a.topics_win.winfo_viewable()
    text = a.topics_text.get("1.0", "end")
    assert "Q3 budget" in text and "Cut travel by 10%" in text and "me: send the revised budget" not in text  # action lines hidden (6552784)
    assert "Traditional Chinese" in a.topics_lang.cget("text")
    _shot(a, "1379_notes_on")


def test_677_wrong_translation_builds_prefilled_url_without_network(make_app, monkeypatch):
    import webbrowser

    from tinglongwu.gui import app as appmod

    a = make_app()
    a.state.apply(("final", "bonjour", "hello there"))
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda u, *x, **k: opened.append(u) or True)
    monkeypatch.setattr(appmod.messagebox, "askokcancel", lambda *x, **k: True)
    monkeypatch.setattr(a, "_wrong_dialog", lambda text, extra: (True, False))
    a.report_wrong()
    assert len(opened) == 1
    url = opened[0]
    assert "template=translation-error.yml" in url
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert "bonjour" in q["happened"][0] and "hello there" in q["happened"][0]
    assert q["langpair"] and q["mode"] and q["version"]


def test_680_use_for_question_asked_once_and_stored_locally(make_app, monkeypatch, tmp_path):
    from tinglongwu.gui import core

    import tkinter as tk

    from tinglongwu.i18n import t

    monkeypatch.setattr(tk.Misc, "focus_force", lambda self: None)  # xvfb has no window manager: the real call can segfault
    a = make_app()
    assert core.usage_should_ask(a.settings)
    a.ask_usage()
    a.root.update()
    first = a.usage_win
    assert first.winfo_exists()
    a.ask_usage()  # a second call reuses the one dialog
    assert a.usage_win is first
    from tkinter import ttk

    def walk(w):
        for c in w.winfo_children():
            yield c
            yield from walk(c)

    widgets = list(walk(first))
    meetings = next(w for w in widgets if isinstance(w, ttk.Radiobutton) and w.cget("value") == "meetings")
    meetings.invoke()
    save = next(w for w in widgets if isinstance(w, ttk.Button) and w.cget("text") == t("gui_usage_ok"))
    save.invoke()  # the dialog's own Save button stores the answer
    assert getattr(a, "usage_win", None) is None and not first.winfo_exists()
    assert not core.usage_should_ask(a.settings)  # never asked again
    a._maybe_ask_usage()
    assert getattr(a, "usage_win", None) is None
    saved = core.load_settings()
    assert saved["usage_use"] == "meetings" and saved["usage_asked"] is True  # stored in the local gui.json
    _shot(a, "680_usage")


def test_940_report_with_ai_copies_prompt_to_clipboard(make_app, monkeypatch):
    import tinglongwu.report as report

    a = make_app()
    monkeypatch.setattr(report, "env_info", lambda: {"Version": "test"})
    a.root.clipboard_clear()  # the wait below must only see the result of this copy
    a.root.update()
    a.copy_ai_prompt()
    for _ in range(100):
        a.poll()  # the real event loop calls this; the worker thread posts ("clip_ai", text) to a.q
        a.root.update()
        try:
            got = a.root.clipboard_get()
        except Exception:  # noqa: BLE001 - nothing copied yet
            got = ""
        if "Version" in got:
            break
        import time

        time.sleep(0.05)
    assert "Version" in got and "test" in got


# -- #1166: the 情境 row is one dropdown ---------------------------------------------------------------------------
def _tk_buttons(w, out=None):
    out = [] if out is None else out
    for c in w.winfo_children():
        if c.winfo_class() in ("TRadiobutton",) and c.cget("style") == "Toolbutton":
            out.append(c)
        _tk_buttons(c, out)
    return out


def test_one_readonly_dropdown_lists_every_scene(make_app):
    from tinglongwu.gui.core import SCENES

    a = make_app()
    assert str(a.scene_combo.cget("state")) == "readonly"
    assert len(a.scene_combo.cget("values")) == len(SCENES)
    assert not _tk_buttons(a.root)  # no scene buttons remain
    assert a.scene_combo.get() == "Watching video (listen only)"  # #1343: a fresh install is listen only


def test_selecting_switches_the_scene_and_saves_it(make_app, tmp_path):
    a = make_app()  # #1343: fresh install = video; switching to Meeting must be saved
    a.scene_combo.current(0)
    a.scene_combo.event_generate("<<ComboboxSelected>>")
    a.root.update()
    assert a.settings["scene"] == "meeting"
    saved = json.loads((tmp_path / "tinglongwu" / "gui.json").read_text("utf-8"))
    assert saved["scene"] == "meeting"


def test_restart_shows_the_saved_scene(make_app):
    a = make_app(scene="video")
    assert a.scene_combo.get() == "Watching video (listen only)"


def test_entries_follow_the_ui_language(make_app):
    from tinglongwu import i18n

    a = make_app(scene="video")
    i18n.set_ui_lang("ja")
    a.relabel()
    assert a.scene_combo.get() == i18n.MESSAGES["ja"]["gui_scene_video"]
    assert tuple(a.scene_combo.cget("values")) == (i18n.MESSAGES["ja"]["gui_scene_meeting"],
                                                   i18n.MESSAGES["ja"]["gui_scene_video"])
    i18n.set_ui_lang("en")


@pytest.mark.parametrize("lang", ["zh-TW", "zh-CN", "ja", "ru", "en"])
def test_scene_strings_exist_in_every_ui_language(lang):
    """Every UI language (zh-CN falls back to the zh-TW table, i18n.normalize) shows two distinct, translated entries."""
    from tinglongwu import i18n

    key = i18n.normalize(lang)
    assert key in i18n.MESSAGES
    names = [i18n.MESSAGES[key].get("gui_scene_" + sc) for sc in ("meeting", "video")]
    assert all(names) and names[0] != names[1]
    if key != i18n.DEFAULT:
        assert names != [i18n.MESSAGES[i18n.DEFAULT]["gui_scene_" + sc] for sc in ("meeting", "video")]


# -- #1241: bottom panel uses the full width; CPU-mode notice shown once --------------------------------------------
def _cpu_session(a):
    """What a CPU-fallback session puts on screen: the fallback notice, then the per-line CPU status (#780)."""
    from tinglongwu.gui.core import cpu_mode_line
    from tinglongwu.i18n import t

    a.v_notice.set(" ".join(t("gpu_fallback", why="no usable NVIDIA GPU found").split()))
    a._dedupe_notice()
    a._status_text(cpu_mode_line("cpu", 701.0, path=("cpu", 8)))
    a._dedupe_notice()
    a.root.update()


@pytest.mark.parametrize("w", [1800, 900])
def test_1241_status_wraps_at_panel_width_and_cpu_mode_once(make_app, monkeypatch, w):
    import tkinter as tk

    from tinglongwu.i18n import t

    monkeypatch.setattr(tk.Misc, "winfo_screenwidth", lambda self: 1920)
    monkeypatch.setattr(tk.Misc, "winfo_screenheight", lambda self: 1080)
    a = make_app()
    a.root.geometry(f"{w}x500+0+0")
    a.root.update()
    _cpu_session(a)
    a.root.update()
    panel = a.status_box.winfo_width()
    assert panel > w * 0.8  # the status box spans the window
    for lbl in (a.lbl_status, a.lbl_notice):
        assert int(str(lbl.cget("wraplength"))) >= panel - 20  # wraps at the panel width, not a fixed 420 px
    shown = a.v_status.get() + "\n" + a.v_notice.get()
    from tinglongwu.gui import core as _core  # #1807: the line is shown once, in whichever view is on

    assert sum(shown.count(x) for x in {t("gui_cpu_mode"), *_core.label_texts("ui.gui_cpu_mode", _core._ui())}) == 1
    assert "\n" not in a.v_notice.get()  # the GPU-fallback reason is one line
    same_row = a.scene_row.winfo_rooty() == a.toggle_row.winfo_rooty()
    assert same_row == (a.bar.winfo_width() >= 1200)  # 情境 + toggles share a row only when wide enough
    assert a.scene_combo.winfo_ismapped() and not a.btn_mic.winfo_ismapped()  # #1604: video scene hides the mic toggle
    if same_row:  # side by side, not drawn on top of each other
        assert a.scene_row.winfo_rootx() + a.scene_row.winfo_width() <= a.toggle_row.winfo_rootx()
    _shot(a, f"1241_{w}x500")


def test_1241_notice_keeps_other_text_when_cpu_mode_dropped():
    from tinglongwu.gui.core import drop_cpu_mode_notice
    from tinglongwu.i18n import t

    cpu = t("gui_cpu_mode")
    status = f"{cpu} - 701 ms"
    assert drop_cpu_mode_notice(f"GPU off | {cpu}", status) == "GPU off"
    assert drop_cpu_mode_notice(f"{cpu}\nMic is off", status) == "Mic is off"
    assert drop_cpu_mode_notice(cpu, "Loading...") == cpu  # status does not show it yet: keep it
