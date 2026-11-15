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
