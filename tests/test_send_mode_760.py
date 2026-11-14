"""#760: countdown / auto send modes with a fake clock; low-confidence lines always wait for "y"."""
import queue

from tinglongwu.reverse import sendmode
from tinglongwu.reverse.confirm import ConfirmSession, Utterance


def _get(inp):
    try:
        return inp.get(timeout=0.3)
    except queue.Empty:
        raise EOFError


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _run(mode, lines, back="你好嗎", zh="你好嗎", extra=None, no_back=False):
    clk, sent, out = Clock(), [], []
    inp = queue.Queue()
    for ln in lines:
        inp.put(ln)

    def readline():
        try:
            return inp.get(timeout=0.3)
        except queue.Empty:
            raise EOFError

    s = ConfirmSession(lambda ru: ([1, 2], 16000), lambda a, sr: sent.append(a), out=out.append,
                       readline=readline, flush=lambda: None, clock=clk, sleep=clk.sleep, mode=mode,
                       countdown_s=2.0, require_back=not no_back, max_age=0)
    u = Utterance(1, zh, "hello", back, extra=extra or {})
    s.submit(u)
    s.finish()
    s.run()
    return u, sent, out, clk


def test_countdown_sends_without_key_press():
    u, sent, out, clk = _run("countdown", [])
    assert u.decision == "sent" and len(sent) == 1
    assert clk.t >= 2.0  # waited the full countdown


def test_countdown_stop_discards():
    u, sent, _, _ = _run("countdown", ["x"])
    assert u.decision == "discarded" and not sent


def test_low_confidence_mismatch_waits_and_discards_on_eof():
    u, sent, out, _ = _run("countdown", [], back="完全不同的句子", zh="你好嗎")
    assert u.decision == "discarded" and not sent
    assert any("low confidence" in o for o in out)


def test_low_confidence_sent_after_explicit_y():
    u, sent, _, _ = _run("countdown", ["y"], extra={"halluc": True})
    assert u.decision == "sent"  # "y" during the countdown sends at once


def test_auto_sends_confident_and_holds_halluc():
    u, sent, _, _ = _run("auto", [])
    assert u.decision == "sent"
    u2, sent2, _, _ = _run("auto", [], extra={"halluc": True})
    assert u2.decision == "discarded" and not sent2


def test_confirm_mode_unchanged():
    u, sent, _, _ = _run("confirm", [""])
    assert u.decision == "discarded"
    u, sent, _, _ = _run("confirm", ["y"])
    assert u.decision == "sent"


def test_low_confidence_reasons():
    mk = lambda **k: Utterance(1, "你好嗎", "x", k.pop("back", "你好嗎"), extra=k)  # noqa: E731
    assert sendmode.low_confidence(mk()) == ""
    assert "logprob" in sendmode.low_confidence(mk(avg_logprob=-1.5))
    assert sendmode.low_confidence(mk(back="")) != ""
    assert sendmode.low_confidence(mk(back=""), require_back=False) == ""


def test_stale_answer_never_decides_next_utterance():
    clk, sent, out, inp = Clock(), [], [], queue.Queue()
    s = ConfirmSession(lambda ru: ([1], 16000), lambda a, sr: sent.append(a), out=out.append,
                       readline=lambda: _get(inp), flush=lambda: None, clock=clk, sleep=clk.sleep,
                       mode="countdown", countdown_s=2.0, max_age=0)
    u1 = Utterance(1, "你好嗎", "a", "你好嗎")
    s.submit(u1)
    assert s._countdown(u1) is True  # timed out, sent
    inp.put("y")  # typed during playback of u1: belongs to u1
    s._reader[0].join(1)
    u2 = Utterance(2, "你好嗎", "b", "完全不同的句子")  # low confidence
    s.submit(u2)
    assert s._countdown(u2) is None  # the stale "y" did not send it
    s.finish()


def test_voice_command_during_countdown_switches_voice():
    clk, voices, inp = Clock(), [], queue.Queue()
    inp.put("v2")
    s = ConfirmSession(lambda ru: ([1], 16000), lambda a, sr: None, out=lambda m: None,
                       readline=lambda: _get(inp), flush=lambda: None, clock=clk, sleep=clk.sleep,
                       set_voice=voices.append, mode="countdown", countdown_s=2.0, max_age=0)
    u = Utterance(1, "你好嗎", "a", "你好嗎")
    s.submit(u)
    assert s._countdown(u) is True and voices == [2]
    s.finish()
