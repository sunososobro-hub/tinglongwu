"""#832: SendDecider (I/O-free send-mode state machine) driven with a fake clock."""
from tinglongwu.reverse import sendmode as sm
from tinglongwu.reverse.confirm import Utterance


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def _u(back="你好嗎", zh="你好嗎", extra=None):
    return Utterance(1, zh, "hello", back, extra=extra or {})


def _d(mode, clk=None, **kw):
    clk = clk or Clock()
    return sm.SendDecider(clk, mode, countdown_s=2.0, **kw), clk


def test_countdown_sends_at_deadline_without_input():
    d, clk = _d("countdown")
    assert d.line_ready(_u()) == sm.WAIT
    clk.t = 1.9
    assert d.tick() == sm.WAIT
    clk.t = 2.0
    assert d.tick() == sm.SEND


def test_stop_before_deadline_discards():
    d, clk = _d("countdown")
    d.line_ready(_u())
    clk.t = 1.0
    assert d.stop() == sm.DISCARD
    clk.t = 5.0
    assert d.tick() == sm.DISCARD


def test_low_confidence_never_sends_without_confirm_in_any_mode():
    bad = _u(back="完全不同的句子")
    assert sm.low_confidence(bad)
    for mode in sm.MODES:
        d, clk = _d(mode)
        act = d.line_ready(bad)
        clk.t = 10.0
        if act == sm.WAIT:
            act = d.tick()
        assert act == sm.ASK, mode
        assert d.tick() == sm.ASK
        assert d.confirm() == sm.SEND


def test_auto_sends_confident_line_at_once():
    d, _ = _d("auto")
    assert d.line_ready(_u()) == sm.SEND


def test_confirm_mode_waits_for_confirm():
    d, clk = _d("confirm")
    assert d.line_ready(_u()) == sm.ASK
    clk.t = 100.0
    assert d.tick() == sm.ASK
    assert d.confirm() == sm.SEND


def test_missing_back_translation_is_low_unless_not_required():
    d, clk = _d("auto")
    assert d.line_ready(_u(back="")) == sm.ASK
    d2, _ = _d("auto", require_back=False)
    assert d2.line_ready(_u(back="")) == sm.SEND


def test_restart_extends_countdown():
    d, clk = _d("countdown")
    d.line_ready(_u())
    clk.t = 1.5
    d.restart()
    clk.t = 3.0
    assert d.tick() == sm.WAIT
    clk.t = 3.5
    assert d.tick() == sm.SEND


def test_back_ready_marks_late_mismatch_low():
    d, clk = _d("countdown")
    u = _u(back="")
    d.line_ready(u)
    u.back = "完全不同的句子"
    d.back_ready(u)
    clk.t = 2.0
    assert d.tick() == sm.ASK
