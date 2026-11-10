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
    assert core.load_settings(p) == core.DEFAULTS  # missing file
    s = core.load_settings(p)
    s.update(lang="ru", target="en", save=True, save_path="x.jsonl", font_size=40, x=10, y=20)
    assert core.save_settings(s, p)
    assert core.load_settings(p) == s
    p.write_text("{not json", "utf-8")
    assert core.load_settings(p) == core.DEFAULTS
    p.write_text(json.dumps({"lang": 5, "font_size": 9999, "save": "yes", "x": True}), "utf-8")
    got = core.load_settings(p)
    assert got["lang"] == "auto" and got["font_size"] == core.FONT_MAX and got["save"] is False and got["x"] is None


def test_save_settings_unwritable(tmp_path):
    blocker = tmp_path / "f"
    blocker.write_text("x")
    assert core.save_settings(core.DEFAULTS, blocker / "gui.json") is False


def test_settings_to_argv_parses():
    from tinglongwu.cli import parse_args

    s = dict(core.DEFAULTS, lang="ru", target="zh-cn", engine="opus", glossary="g.txt", save=True)
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
