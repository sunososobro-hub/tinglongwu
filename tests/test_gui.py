"""GUI logic (no display needed) + the soundcard warning filter."""
import json
import queue
import threading
import warnings

import pytest

from tinglongwu.gui import core
from tinglongwu.sink import Result


def test_queue_sink_final():
    q = queue.Queue()
    core.QueueSink(q).write(Result("ru", "privet", None, "你好", "zh"))
    core.QueueSink(q).write(Result("zh", "你好", None, "", "zh"))  # same language: show the source
    assert q.get_nowait() == ("final", "privet", "你好")
    assert q.get_nowait() == ("final", "你好", "你好")


def test_draft_renderer_events():
    q = queue.Queue()
    r = core.QueueDraftRenderer(q)
    r.draw("RU", "hello there", "my friend", "ZH", "你好世界", show_src=False)
    r.draw("RU", "privet", "mir", "ZH", "", show_src=False)  # nothing translated yet: no event
    r.draw("ZH", "你好", "", "ZH", "", show_src=True)  # same language: source is the draft
    r.clear()
    assert [q.get_nowait() for _ in range(3)] == [
        ("draft", "hello there my friend", "你好世界"), ("draft", "", "你好"), ("clear",)]
    assert q.empty()


def test_subtitle_state_draft_replaced_by_final():
    st = core.SubtitleState(2)
    assert st.apply(("draft", "a", "草稿一"))
    assert st.lines() == [("草稿一", True)]
    st.apply(("clear",))
    st.apply(("final", "a", "最終一"))
    assert st.lines() == [("最終一", False)] and st.source() == "a"
    st.apply(("draft", "b", "草稿二"))
    assert st.lines() == [("最終一", False), ("草稿二", True)]
    st.apply(("final", "b", "最終二"))
    st.apply(("final", "c", "最終三"))
    assert st.lines() == [("最終二", False), ("最終三", False)]  # only the last 2
    assert st.last_text() == "最終三"
    assert not st.apply(("clear",))
    assert not st.apply(("status", "x"))


def test_settings_roundtrip_and_corruption(tmp_path):
    p = tmp_path / "sub" / "gui.json"
    # missing file; gpu=True because the first-run live-draft default follows the GPU (#89)
    assert core.load_settings(p, gpu=lambda: True) == core.DEFAULTS
    s = core.load_settings(p)
    s.update(lang="ru", target="en", save=True, save_path="x.jsonl", font_size=40, x=10, y=20)
    assert core.save_settings(s, p)
    assert core.load_settings(p) == s
    p.write_text("{not json", "utf-8")
    assert core.load_settings(p, gpu=lambda: True) == core.DEFAULTS
    p.write_text(json.dumps({"lang": 5, "font_size": 9999, "save": "yes", "x": True}), "utf-8")
    got = core.load_settings(p)
    assert got["lang"] == "auto" and got["font_size"] == core.FONT_MAX and got["save"] is False and got["x"] is None


def test_save_settings_unwritable(tmp_path):
    blocker = tmp_path / "f"
    blocker.write_text("x")
    assert core.save_settings(core.DEFAULTS, blocker / "gui.json") is False


def test_settings_to_argv_parses():
    from tinglongwu.cli import parse_args

    s = dict(core.DEFAULTS, lang="ru", target="zh-cn", quality="manual", engine="opus", glossary="g.txt", save=True)
    a = parse_args(core.settings_to_argv(s))
    assert (a.lang, a.to, a.zh_variant, a.engine, a.glossary, a.save) == ("ru", "zh", "cn", "opus", "g.txt", "")
    a = parse_args(core.settings_to_argv(dict(core.DEFAULTS, target="en", save=True, save_path="o.jsonl")))
    assert (a.to, a.save) == ("en", "o.jsonl")
    assert parse_args(["--gui"]).gui and not parse_args([]).gui


def test_pipeline_stop_event():
    import numpy as np
    from tinglongwu.capture.base import AudioSource
    from tinglongwu.pipeline import run
    from tinglongwu.segment import Segmenter

    class Silent(AudioSource):
        def frames(self):
            while True:
                yield np.zeros(1600, dtype=np.float32)

    stop = threading.Event()
    t = threading.Thread(target=lambda: run(Silent(), Segmenter(4.0, False), None, None, core.QueueSink(queue.Queue()),
                                            stop_event=stop), daemon=True)
    t.start()
    stop.set()
    t.join(3)
    assert not t.is_alive()


def test_soundcard_filter_survives_late_always_filter():
    """soundcard does simplefilter('always', SoundcardRuntimeWarning) at import, AFTER our filter was
    registered, which re-enabled the warning. suppress_capture_warnings() must win when called afterwards."""
    from tinglongwu.capture._soundcard import suppress_capture_warnings

    class SoundcardRuntimeWarning(RuntimeWarning):
        pass

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        suppress_capture_warnings()
        warnings.simplefilter("always", SoundcardRuntimeWarning)  # what the soundcard import does
        suppress_capture_warnings()  # called again after the import by SoundcardSource.frames()
        warnings.warn("data discontinuity in recording", SoundcardRuntimeWarning)
        warnings.warn("something else", RuntimeWarning)
    assert [str(x.message) for x in w] == ["something else"]


def test_overlay_constructs_headless_or_skips():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.destroy()
    from tinglongwu.gui import app

    a = app.App()
    try:
        a.overlay.show()
        a.state.apply(("final", "s", "字幕"))
        a.overlay.render(a.state)
        a.root.update()
        assert a.overlay.text.get("1.0", "end-1c") == "字幕"
        a.overlay.bump_font(4)
        assert a.settings["font_size"] == core.DEFAULTS["font_size"] + 4
    finally:
        a.root.destroy()


def test_target_follows_system_locale(tmp_path, monkeypatch):
    from tinglongwu import i18n
    from tinglongwu.gui.core import load_settings, save_settings, target_for_locale

    assert target_for_locale("zh_CN.UTF-8") == "zh-cn"
    assert target_for_locale("zh-Hant-TW") == "zh-tw"
    assert target_for_locale("en_US.UTF-8") == "en"
    assert target_for_locale("") == "zh-tw"
    monkeypatch.setattr(i18n, "_system_tag", lambda: "en-US")
    p = tmp_path / "gui.json"
    assert load_settings(p)["target"] == "en"  # no saved file: follows locale
    s = load_settings(p)
    s["target"] = "zh-cn"
    save_settings(s, p)
    assert load_settings(p)["target"] == "zh-cn"  # saved choice wins


def test_paused_state_freezes_redraw_but_keeps_events():
    from tinglongwu.gui.core import SubtitleState

    st = SubtitleState(2)
    assert st.apply(("final", "a", "one")) is True
    st.paused = True
    assert st.apply(("final", "b", "two")) is False  # no redraw while paused ...
    st.paused = False
    assert st.lines() == [("one", False), ("two", False)]  # ... but nothing was lost


def test_subtitle_history_keeps_200_finals_and_draft_last():
    """#72: the subtitle bar scrolls back through the last HISTORY_LINES finals; the draft is the newest row."""
    st = core.SubtitleState(core.HISTORY_LINES)
    for i in range(250):
        st.apply(("final", f"s{i}", f"t{i}"))
    st.apply(("draft", "sd", "td"))
    h = st.history()
    assert len(h) == core.HISTORY_LINES + 1
    assert h[0] == ("s50", "t50", False)  # oldest 50 dropped
    assert h[-2] == ("s249", "t249", False) and h[-1] == ("sd", "td", True)
    st.apply(("final", "sd", "tf"))  # the draft is replaced in place by its final
    assert st.history()[-1] == ("sd", "tf", False) and len(st.history()) == core.HISTORY_LINES


def test_follow_newest_only_at_bottom():
    assert core.follow_newest(1.0)
    assert not core.follow_newest(0.8)  # user scrolled up to re-read: do not jump to the newest line


def test_rows_after_drag_and_clamp():
    assert core.rows_after_drag(3, -30, 30) == 4  # top edge dragged up 1 line -> 1 more row
    assert core.rows_after_drag(3, 31, 30) == 2
    assert core.rows_after_drag(4, -10_000, 30) == core.ROWS_MAX
    assert core.rows_after_drag(4, 10_000, 30) == core.ROWS_MIN
    assert core.rows_after_drag(4, -60, 0) == 4
    assert (core.ROWS_MIN, core.ROWS_MAX, core.FONT_MIN, core.FONT_MAX) == (1, 5, 12, 72)


def test_font_preset_nearest_label():
    assert [core.font_preset(core.FONT_PRESETS[k]) for k in ("S", "M", "L", "XL")] == ["S", "M", "L", "XL"]
    assert core.font_preset(30) == "M" and core.font_preset(90) == "XL" and core.font_preset(5) == "S"


def test_overlay_lines_setting_persists_and_clamps(tmp_path):
    p = tmp_path / "gui.json"
    s = core.load_settings(p)
    assert s["overlay_lines"] == 3  # default 3 lines
    s.update(overlay_lines=1, font_size=14)
    core.save_settings(s, p)
    got = core.load_settings(p)
    assert got["overlay_lines"] == 1 and got["font_size"] == 14  # survive restart
    s.update(overlay_lines=5, font_size=40)
    core.save_settings(s, p)
    got = core.load_settings(p)
    assert got["overlay_lines"] == 5 and got["font_size"] == 40
    p.write_text(json.dumps({"font_size": 5}), "utf-8")
    assert core.load_settings(p)["font_size"] == 12
    p.write_text(json.dumps({"overlay_lines": 999}), "utf-8")
    assert core.load_settings(p)["overlay_lines"] == core.ROWS_MAX


def test_gui_strings_exist_in_all_three_languages():
    from tinglongwu import i18n

    gui_keys = {k for k in i18n.MESSAGES["en"] if k.startswith("gui_")}
    assert gui_keys
    for lang in ("en", "ru", "zh-TW"):
        assert gui_keys <= set(i18n.MESSAGES[lang]), lang
    assert len({i18n.MESSAGES[lg]["gui_start"] for lg in ("en", "ru", "zh-TW")}) == 3


def test_set_ui_lang_switches_t_and_persists(tmp_path, monkeypatch):
    from tinglongwu import i18n, userdirs

    monkeypatch.setenv("TLWU_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(i18n, "_override", None)
    for lang, start in (("zh-TW", "開始"), ("ru", "Старт"), ("en", "Start")):
        assert i18n.set_ui_lang(lang) == lang
        assert i18n.t("gui_start") == start
        assert userdirs.read_config()["ui_lang"] == lang
    assert i18n.t("gui_error", msg="boom") == "Error: boom"


def test_gui_module_imports_and_language_names():
    from tinglongwu.gui import app

    assert set(app.UI_LANGS.values()) == {"zh-TW", "en", "ru"}
    assert core.DEFAULTS["theme"] == "dark"


# -- #72: subtitles inside the main window (scrolling log), floating bar optional and off by default ---------

def test_floating_bar_off_by_default_and_log_font_persist(tmp_path):
    p = tmp_path / "gui.json"
    s = core.load_settings(p)
    assert s["floating_bar"] is False and s["log_font_size"] == 14
    s.update(floating_bar=True, log_font_size=22)
    core.save_settings(s, p)
    got = core.load_settings(p)
    assert got["floating_bar"] is True and got["log_font_size"] == 22
    p.write_text(json.dumps({"log_font_size": 9999, "floating_bar": "yes"}), "utf-8")
    got = core.load_settings(p)
    assert got["log_font_size"] == core.FONT_MAX and got["floating_bar"] is False


@pytest.fixture
def gui_app(tmp_path, monkeypatch):
    """A real App on a real (virtual) display with a worker that just waits for Stop; settings in tmp_path."""
    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    from tinglongwu import i18n
    from tinglongwu.gui import app

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(i18n, "_system_tag", lambda: "en-US")
    monkeypatch.setattr(i18n.userdirs, "write_config_value", lambda k, v: None)  # never touch the real config
    monkeypatch.setattr(i18n, "_override", "en")  # restored after the test

    def fake_work(self, s, stop):
        stop.wait(10)
        self.q.put(("stopped",))

    monkeypatch.setattr(app.App, "_work", fake_work)
    a = app.App()
    a.root.update()
    yield a
    a.stop_ev.set()
    a.root.destroy()


def _log(a):
    a.root.update()
    return a.log_text.get("1.0", "end-1c")


def test_start_shows_listening_in_main_window_and_bar_stays_hidden(gui_app):
    a = gui_app
    assert _log(a) == ""
    a.toggle()  # Start
    assert _log(a) == "Listening..."
    assert not a.overlay.visible and a.overlay.win.state() == "withdrawn"  # floating bar: default OFF
    a.q.put(("final", "privet", "你好"))
    a.q.put(("draft", "kak dela", "你好嗎"))
    a.poll()
    assert _log(a) == "你好\n你好嗎"
    a.var_show_src.set(True)
    a._src_toggled()
    assert _log(a) == "privet\n你好\nkak dela\n你好嗎"
    a.toggle()  # Stop
    a.worker.join(5)
    a.poll()
    assert _log(a) == "privet\n你好\nkak dela\n你好嗎"  # the history stays readable after Stop
    assert a.settings["floating_bar"] is False


def test_floating_bar_toggle_shows_bar_with_listening(gui_app):
    a = gui_app
    a.v_bar.set(True)
    a._bar_toggled()
    assert not a.overlay.visible  # not running yet: nothing to show
    a.toggle()
    a.root.update()
    assert a.overlay.visible and a.overlay.win.state() == "normal"
    assert a.overlay.text.get("1.0", "end-1c") == "Listening..."
    a.q.put(("final", "s", "字幕"))
    a.poll()
    a.root.update()
    assert a.overlay.text.get("1.0", "end-1c") == "字幕"
    a.v_bar.set(False)
    a._bar_toggled()
    assert not a.overlay.visible and _log(a) == "字幕"  # log keeps working without the bar
    assert core.load_settings()["floating_bar"] is True  # saved at Start (settings live in tmp_path)


def test_log_follows_newest_unless_scrolled_up(gui_app):
    a = gui_app
    a.toggle()
    for i in range(250):
        a.q.put(("final", f"s{i}", f"line {i}"))
    a.poll()
    text = _log(a)
    assert text.startswith("line 50\n") and text.endswith("line 249")  # 200 kept
    assert a.log_text.dlineinfo("end-1c") is not None  # newest line is on screen
    a.log_text.yview("20.0")  # user scrolls back
    a.root.update()
    top = a.log_text.index("@0,0")
    a.q.put(("draft", "", "newer draft"))
    a.poll()
    a.root.update()
    assert a.log_text.index("@0,0") == top  # stays where the user is reading
    assert a.log_text.dlineinfo("end-1c") is None
    a.log_text.yview_moveto(1.0)  # back at the bottom: follows again
    a.root.update()
    a.q.put(("final", "", "newest"))
    a.poll()
    a.root.update()
    assert a.log_text.dlineinfo("end-1c") is not None and _log(a).endswith("newest")


def test_log_font_buttons_and_pause(gui_app):
    a = gui_app
    w0 = a.root.winfo_reqwidth()
    a.bump_log_font(8)
    a.root.update()
    assert a.settings["log_font_size"] == 22 and a.root.winfo_reqwidth() == w0  # window does not grow
    assert str(a.log_text.tag_cget("final", "font")).split()[-1] == "22"
    a.toggle()
    a.set_paused(True)
    a.q.put(("final", "s", "while paused"))
    a.poll()
    assert _log(a) == "Listening..."  # frozen
    a.set_paused(False)
    assert _log(a) == "while paused"  # nothing lost
    a.v_ui_lang.set("繁體中文")
    a.state = core.SubtitleState(core.HISTORY_LINES)
    a.switch_lang()
    assert _log(a) == "聆聽中…"


def test_start_is_disabled_and_counts_seconds_until_models_are_ready(gui_app):
    """#332: while models load the button reads "Loading... N s" and cannot be pressed; ready enables Stop."""
    import time

    from tinglongwu import cli

    a = gui_app
    a.toggle()  # Start
    assert str(a.btn.cget("state")) == "disabled" and a.btn.cget("text") == "Loading... 0 s"
    a.q.put(("status", "loading speech model... (0.0 s)"))
    a.load_t0 = time.monotonic() - 5.2  # 5 s into loading
    a.poll()
    assert a.v_status.get() == "loading speech model... (0.0 s)"
    assert str(a.btn.cget("state")) == "disabled" and a.btn.cget("text") == "Loading... 5 s"
    a.q.put(("status", f"{cli.READY_STATUS} - loaded in 5.2 s (speech model 5.2 s)"))
    a.poll()
    assert str(a.btn.cget("state")) == "normal" and a.btn.cget("text") == "Stop"
    a.poll()
    assert a.btn.cget("text") == "Stop"  # the ticker stopped
    a.toggle()  # Stop
    a.worker.join(5)
    a.poll()
    assert str(a.btn.cget("state")) == "normal" and a.btn.cget("text") == "Start"


def test_download_finishing_while_loading_keeps_the_button_disabled(gui_app, monkeypatch):
    """#332 / CodeRabbit on #452: a model download that ends mid-load must not re-enable the Loading button."""
    from tinglongwu import cli
    from tinglongwu.gui import app as app_mod

    monkeypatch.setattr(app_mod, "is_downloaded", lambda m: True)
    a = gui_app
    a.toggle()  # Start: loading
    a._download_done("tiny", 0)
    assert str(a.btn.cget("state")) == "disabled"
    a.q.put(("status", f"{cli.READY_STATUS} - loaded in 1.0 s (speech model 1.0 s)"))
    a.poll()
    assert str(a.btn.cget("state")) == "normal"
    a._download_done("tiny", 0)  # after ready: unchanged behaviour, the button works
    assert str(a.btn.cget("state")) == "normal"
