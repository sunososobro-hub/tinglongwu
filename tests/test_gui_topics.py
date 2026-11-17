"""#611 (#595 part 4): the GUI topic panel model lists each finished topic; the default follows CUDA / bake-off."""
from tinglongwu import topics
from tinglongwu.gui.topics_panel import TopicPanel, topics_default


def _summary(n):
    return f"TITLE: Topic {n}\n- point {n}a\n- point {n}b\nACTION me: do {n}\n"


def _feed(notes, n_topics, per_topic=14, step=5.0, pause=12.0):
    t = 0.0
    for k in range(n_topics):
        for i in range(per_topic):
            notes.on_line({"t": t, "side": "me" if i % 2 else "them", "src": f"s{k}.{i}", "dst": f"line {k}.{i}"})
            t += step
        t += pause


def test_panel_lists_three_topics_with_title_and_bullets(tmp_path):
    panel, changes = TopicPanel(), []
    panel.on_change = lambda: changes.append(len(panel.entries()))
    counter = iter(range(1, 10))
    notes = topics.TopicNotes(tmp_path / "m.topics.md", lambda prompt: _summary(next(counter)),
                              lower_priority=lambda: False, on_topic=panel.add)
    _feed(notes, 3)
    notes.close(timeout=10)
    got = panel.entries()
    assert [e["title"] for e in got] == ["Topic 1", "Topic 2", "Topic 3"]
    assert got[0]["bullets"] == ["point 1a", "point 1b"] and got[0]["actions"] == ["me: do 1"]
    assert changes == [1, 2, 3]
    assert (tmp_path / "m.topics.md").read_text("utf-8").count("## ") == 3  # the file notes still written


def test_broken_panel_does_not_stop_notes(tmp_path, capsys):
    def boom(_t):
        raise RuntimeError("redraw failed")
    notes = topics.TopicNotes(tmp_path / "n.topics.md", lambda p: _summary(1), lower_priority=lambda: False,
                              on_topic=boom)
    _feed(notes, 2)
    notes.close(timeout=10)
    assert (tmp_path / "n.topics.md").read_text("utf-8").count("## ") == 2
    assert "topic panel failed" in capsys.readouterr().err


def test_default_on_with_cuda_off_on_cpu_without_table():
    assert topics_default(True) is True
    assert topics_default(False) is False
    assert topics_default(False, []) is False


def test_cpu_default_follows_bakeoff_rows():
    fits = [{"model": "a", "subtitle_p50_delta_pct": 25.0}, {"model": "b", "subtitle_p50_delta_pct": 9.9}]
    assert topics_default(False, fits) is True
    assert topics_default(False, [fits[0]]) is False
    assert topics_default(True, [fits[0]]) is True


def test_panel_clear():
    panel = TopicPanel()
    panel.add(topics.Topic("x", ["b"], [], 0, 65))
    assert panel.entries()[0]["when"] == "00:00-01:05"
    panel.clear()
    assert panel.entries() == []


# --- #611 part 4 remainder: settings -> --topics, and the Tk topic notes window ---

def test_topics_flag_only_with_model_save_and_switch(tmp_path):
    from tinglongwu.gui.core import DEFAULTS, settings_to_argv, topics_on

    gguf = tmp_path / "m.gguf"
    gguf.write_bytes(b"GGUF")
    s = dict(DEFAULTS, save=True, topics=True, topics_model=str(gguf))
    argv = settings_to_argv(s)
    assert argv[argv.index("--topics") + 1] == str(gguf)
    unsaved = settings_to_argv(dict(s, save=False))  # #1379: the notes switch alone saves the transcript
    assert "--save" in unsaved and "--topics" in unsaved
    assert "--topics" not in settings_to_argv(dict(s, topics=False))
    assert "--topics" not in settings_to_argv(dict(s, topics_model=str(tmp_path / "missing.gguf")))
    unset = dict(s, topics=None)  # no choice yet: follows topics_default (CUDA on, CPU-only off)
    assert topics_on(unset, has_cuda=True) is True
    assert topics_on(unset, has_cuda=False) is False


def test_topics_choice_survives_restart(tmp_path):
    import json

    from tinglongwu.gui.core import load_settings

    p = tmp_path / "gui.json"
    p.write_text(json.dumps({"topics": False, "topics_model": "C:/m.gguf"}), "utf-8")
    s = load_settings(p, gpu=lambda: True)
    assert s["topics"] is False and s["topics_model"] == "C:/m.gguf"
    p.write_text(json.dumps({"topics": "yes"}), "utf-8")
    assert load_settings(p, gpu=lambda: True)["topics"] is None


def _tk_app(tmp_path, monkeypatch):
    import pytest

    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    from tinglongwu import i18n
    from tinglongwu.gui import app as appmod

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(i18n, "_system_tag", lambda: "en-US")
    monkeypatch.setattr(i18n.userdirs, "write_config_value", lambda k, v: None)
    monkeypatch.setattr(i18n, "_override", "en")
    return appmod.App()


def test_gui_window_lists_three_topics_from_the_summarizer(tmp_path, monkeypatch):
    a = _tk_app(tmp_path, monkeypatch)
    try:
        counter = iter(range(1, 10))
        notes = topics.TopicNotes(tmp_path / "g.topics.md", lambda prompt: _summary(next(counter)),
                                  lower_priority=lambda: False, on_topic=a.topic_panel.add)
        _feed(notes, 3)
        notes.close(timeout=10)
        a.poll()  # drains the ("topics",) events posted on the summarizer thread
        assert a.topics_win is not None and a.topics_win.winfo_exists()
        text = a.topics_text.get("1.0", "end")
        for n in (1, 2, 3):
            assert f"Topic {n}" in text and f"point {n}a" in text and f"me: do {n}" in text
        assert a.topics_hint.cget("text") == ""
    finally:
        a.root.destroy()


def test_gui_window_says_what_is_missing_and_switch_is_saved(tmp_path, monkeypatch):
    from tinglongwu.gui.core import load_settings

    a = _tk_app(tmp_path, monkeypatch)
    try:
        a.settings.update(topics_model="", save=False)
        a.render_topics(show=True)
        assert "GGUF" in a.topics_hint.cget("text")
        model = tmp_path / "m.gguf"
        model.write_bytes(b"GGUF")
        a.settings.update(topics_model=str(model), topics=True)
        a.render_topics()
        assert a.topics_hint.cget("text") == "Each finished topic shows up here during the meeting."  # #1379: no Save needed
        a.v_topics.set(False)
        a._topics_toggled()
        assert load_settings(gpu=lambda: True)["topics"] is False
    finally:
        a.root.destroy()


def test_listen_leg_passes_the_panel_to_run_session(tmp_path, monkeypatch):
    import threading

    from tinglongwu import cli
    from tinglongwu.gui.core import DEFAULTS

    a = _tk_app(tmp_path, monkeypatch)
    try:
        got = {}
        monkeypatch.setattr(cli, "run_session", lambda args, **kw: got.update(kw))
        a._run_leg(dict(DEFAULTS), threading.Event())
        assert got["on_topic"] == a.topic_panel.add
        got.clear()
        a._run_leg(dict(DEFAULTS), threading.Event(), sink_cls=lambda q: type("S", (), {})())
        assert got["on_topic"] is None  # the speak leg never feeds the panel
    finally:
        a.root.destroy()
