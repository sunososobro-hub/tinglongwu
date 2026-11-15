from tinglongwu.reverse.echoguard import EchoGuard, similarity


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def _g():
    c = Clock()
    logs = []
    return EchoGuard(clock=c, log=logs.append), c, logs


def test_echo_dropped_and_logged():
    g, c, logs = _g()
    g.finished(g.started("Hello, can you hear me?"))
    c.t += 2
    assert g.check("hello can you hear me")
    assert logs == ["tlwu: listen gate: dropped echo"]


def test_partial_echo():
    g, c, _ = _g()
    g.finished(g.started("We would like to schedule the meeting for next Tuesday afternoon"))
    c.t += 1
    assert g.is_echo("schedule the meeting for next Tuesday")
    assert g.is_echo("We would like to schedule the")


def test_genuine_reply_passes():
    g, c, _ = _g()
    g.finished(g.started("Can you hear me?"))
    c.t += 2
    assert not g.is_echo("Yes, I can hear you now")
    assert not g.is_echo("Yes", "我現在聽得到你")
    assert similarity("Yes, I can hear you now", "Can you hear me?") < 0.6


def test_source_text_and_cjk():
    g, c, _ = _g()
    g.note("你聽得到我嗎")
    c.t += 1
    assert g.is_echo("你聽得到我嗎?")
    assert g.is_echo("聽得到我嗎")
    assert not g.is_echo("對，我聽得到")


def test_window_expires_after_playback_end_only():
    g, c, _ = _g()
    it = g.started("Thank you very much for calling")
    c.t += 30  # still playing: never expires
    assert g.is_echo("thank you very much for calling")
    g.finished(it)
    c.t += 7
    assert g.is_echo("thank you very much for calling")
    c.t += 2
    assert not g.is_echo("thank you very much for calling")


def test_empty():
    g, _, _ = _g()
    assert not g.is_echo("anything")
    assert g.started("") is None
