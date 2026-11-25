"""#1605: one bottom row, an idle hint naming the language pair, and speaker / source / translation order in a line."""
import pytest


@pytest.fixture
def app_1605(tmp_path, monkeypatch):
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
    a.root.update()
    yield a
    a.root.destroy()


def test_subtitle_layout_idle_hint_names_source_and_target(app_1605):
    a = app_1605
    a.render_log()
    shown = a.log_text.get("1.0", "end-1c")
    from tinglongwu import langs
    from tinglongwu.gui.core import choice_label

    src, dst = a._home_pair()
    assert langs.plain_label(src, "en") in shown
    assert choice_label("target", dst, "en") in shown
    a.v_target.set("ja")
    a.render_log()
    assert "Japanese" in a.log_text.get("1.0", "end-1c")


def test_subtitle_layout_line_order_speaker_source_translation(app_1605):
    a = app_1605
    a.state.apply(("final", "hello there", "你好"))
    a.render_log()
    txt = a.log_text.get("1.0", "end-1c")
    assert "Other side" in txt  # the side heading comes first, then the small source, then the large translation
    assert txt.index("Other side") < txt.index("hello there") < txt.index("你好")
    assert a.log_text.tag_ranges("other_src") and a.log_text.tag_ranges("other_final")


def test_subtitle_layout_bottom_controls_share_one_row(app_1605):
    from tinglongwu import i18n
    from tinglongwu.gui.core import SCENES

    a = app_1605
    a.scene_combo.current(SCENES.index("meeting"))  # two-way: the mic toggle is shown too (widest case)
    a.scene_combo.event_generate("<<ComboboxSelected>>")
    for ui in ("English", "Русский", "繁體中文", "日本語"):
        a.v_ui_lang.set(ui)
        a.switch_lang()
        a.root.update()
        assert a.toggle_row.grid_info()["row"] == a.action_row.grid_info()["row"], ui
        assert a.bar.winfo_reqwidth() <= 560, (ui, a.bar.winfo_reqwidth())
    a.v_ui_lang.set("English")
    a.switch_lang()
    assert a.btn.cget("text") == i18n.t("gui_start")
    from tinglongwu.gui.core import BOTH_MODE

    a.listening, a.quick_mode = True, BOTH_MODE  # while running the same button says Stop
    a._sync_quick()
    assert a.btn.cget("text") == i18n.t("gui_stop")
    a.listening = False
