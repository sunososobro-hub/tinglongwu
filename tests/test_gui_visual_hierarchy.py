"""#1644: the newest finished line stands out, speaker names lose the underline, the notes window follows the theme."""
import pytest


@pytest.fixture
def app_1644(tmp_path, monkeypatch):
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


def _latest_text(a):
    r = a.log_text.tag_ranges("other_latest")
    return "".join(a.log_text.get(r[i], r[i + 1]) for i in range(0, len(r), 2))


def test_only_the_newest_finished_line_is_latest(app_1644):
    a = app_1644
    a.state.apply(("final", "first line", "第一句"))
    a.state.apply(("final", "second line", "第二句"))
    a.render_log()
    shown = _latest_text(a)
    assert "第二句" in shown and "第一句" not in shown
    assert a.log_text.tag_ranges("other_final")  # the normal bubble tag stays on every finished line


def test_latest_colour_wins_over_the_normal_bubble(app_1644):
    from tinglongwu.gui.app import BUBBLE

    a = app_1644
    a.state.apply(("final", "hello", "你好"))
    a.render_log()
    pal = BUBBLE["dark" if a.settings.get("theme") != "light" else "light"]
    assert str(a.log_text.tag_cget("other_latest", "background")) == pal["latest"][0]
    names = a.log_text.tag_names()
    assert names.index("other_latest") > names.index("other_final")  # later in the list = higher priority


def test_notes_window_follows_theme_and_marks_bullets(app_1644):
    from tinglongwu import topics
    from tinglongwu.gui.app import BUBBLE, NOTES

    a = app_1644
    a.topic_panel.add(topics.Topic("Plan", ["one point"], [("me", "do it")], 0, 65))
    for theme in ("light", "dark"):
        a.apply_theme(theme)
        a.render_topics(show=True)
        a.root.update()
        tx = a.topics_text
        assert str(tx.cget("bg")) == NOTES[theme]["bg"]  # also after sv_ttk's own recolouring has run
        assert str(a.log_text.cget("bg")) == BUBBLE[theme]["bg"]
        assert str(tx.tag_cget("when", "foreground")) == NOTES[theme]["when"]
        marks = tx.tag_ranges("mark")
        assert [tx.get(marks[i], marks[i + 1]) for i in range(0, len(marks), 2)] == ["•"]  # #1633: the action line is not shown
        assert "one point" in tx.get("1.0", "end") and "me: do it" not in tx.get("1.0", "end")
