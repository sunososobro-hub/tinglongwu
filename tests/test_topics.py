

def test_parse_summary_drops_none_actions():
    from tinglongwu.topics import parse_summary

    tp = parse_summary("TITLE: x\n- a\nACTION me: 無\nACTION them: none\nACTION me: send the file", 0, 1)
    assert tp.actions == [("me", "send the file")]


def test_panel_state_reports_busy_seconds():
    from tinglongwu.gui.topics_panel import TopicPanel

    seen = []
    p = TopicPanel(on_change=lambda: seen.append(1))
    assert p.busy_for() is None
    p.state(True)
    assert p.busy_for() is not None
    p.state(False)
    assert p.busy_for() is None and len(seen) == 2
