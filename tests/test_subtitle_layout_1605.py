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
