import threading
import time
from collections import deque
from types import SimpleNamespace

import numpy as np

from tinglongwu.reverse.confirm import ConfirmSession, Utterance, back_translate


def make(keys, synth=None):
    sent, prev, log = [], [], []
    calls = []

    def default_synth(ru):
        calls.append(ru)
        return np.ones(4, dtype=np.float32), 24000

    it = iter(keys)

    def rl():
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    s = ConfirmSession(synth or default_synth, lambda a, sr: sent.append((len(a), sr)),
                       lambda a, sr: prev.append(sr), out=log.append, readline=rl, flush=lambda: None)
    return s, sent, prev, log, calls


def run(s, utts):
    for u in utts:
        s.submit(u)
    s.finish()
    return s.run()


def test_y_sends_r_and_x_discard_in_order():
    s, sent, prev, log, calls = make(["y", "r", "x", "Y"])
    items = run(s, [Utterance(i, f"z{i}", f"r{i}", f"b{i}") for i in range(1, 5)])
    assert [u.decision for u in items] == ["sent", "discarded", "discarded", "sent"]
    assert len(sent) == 2 and prev == [24000, 24000]
    shown = [m for m in log if "→ r" in m]
    assert [m.split("→ r")[1].split()[0] for m in shown] == ["1", "2", "3", "4"]
    assert " z1" in shown[0] and "→ r1" in shown[0] and "BACK(zh): b1" in shown[0]


def test_voice_switch_resynthesises_then_sends_new_voice():
    voice = {"v": 4}
    s, sent, prev, log, calls = make(["v1", "y", "v9x", "y"],
                                     synth=lambda ru: (np.full(voice["v"] + 1, 1, np.float32), 22050))
    s.set_voice = lambda n: voice.__setitem__("v", n)
    items = run(s, [Utterance(1, "z1", "r1"), Utterance(2, "z2", "r2")])
    assert [u.decision for u in items] == ["sent", "discarded"]  # "v9x" is not a voice key -> discard
    assert sent == [(2, 22050)]  # voice 1 -> length 2
    assert "voice -> 1" in log


def test_voice_keys_ignored_without_set_voice():
    s, sent, *_ = make(["v1"])
    items = run(s, [Utterance(1, "z", "r")])
    assert items[0].decision == "discarded" and sent == []


def test_nothing_sent_before_confirmation():
    gate = threading.Event()
    s, sent, *_ = make([])
    s.readline = lambda: (gate.wait(5), "y")[1]
    t = threading.Thread(target=lambda: run(s, [Utterance(1, "a", "b")]))
    t.start()
    time.sleep(0.3)
    assert sent == []  # synthesized in background, still unsent
    gate.set()
    t.join(5)
    assert len(sent) == 1


def test_queue_keeps_utterances_while_waiting():
    s, sent, *_ = make(["y", "y", "y"])
    items = run(s, [Utterance(i, "z", "r") for i in range(3)])
    assert len(items) == 3 and len(sent) == 3


def test_unknown_key_and_eof_do_not_send():
    s, sent, *_ = make(["hello"])  # then EOF
    items = run(s, [Utterance(1, "a", "b"), Utterance(2, "c", "d")])
    assert sent == [] and [u.decision for u in items] == ["discarded", "discarded"]


def test_no_audio_and_no_preview():
    s, sent, prev, *_ = make(["y"], synth=lambda ru: None)
    items = run(s, [Utterance(1, "a", "b")])
    assert items[0].decision == "no_audio" and sent == [] and prev == []


def test_no_preview_device_ok():
    s, sent, *_ = make(["y"])
    s.preview = None
    run(s, [Utterance(1, "a", "b")])
    assert len(sent) == 1


def test_back_translate_preserves_history():
    class MT:
        history = deque([("zh1", "ru1")], maxlen=4)
        _pair = ("zh", "ru")

        def translate(self, text, src, tgt):
            assert (src, tgt) == ("ru", "zh")
            self.history.clear()
            self._pair = ("ru", "zh")
            self.history.append((text, "x"))
            return SimpleNamespace(text="回譯")

    mt = MT()
    assert back_translate(mt, "привет") == "回譯"
    assert list(mt.history) == [("zh1", "ru1")] and mt._pair == ("zh", "ru")


# --- #248: a bare Enter discards, only "y" sends, type-ahead is flushed before every prompt ---

def test_bare_enter_discards_and_never_sends():
    s, sent, prev, log, calls = make(["", "  ", "yes", "n"])
    items = run(s, [Utterance(i, "z", "r") for i in range(4)])
    assert [u.decision for u in items] == ["discarded"] * 4
    assert sent == [] and prev == []


def test_prompt_says_y_sends_enter_discards():
    s, _, _, log, _ = make(["y"])
    run(s, [Utterance(1, "z", "r")])
    assert any("[y]+Enter=send" in m and "[Enter]=discard" in m for m in log)


class FakeTerm:
    """Line-buffered terminal. Lines typed ahead sit in `buf` (read first); once it is empty the user types the
    next scripted answer after seeing the prompt. flush() is what tcflush(TCIFLUSH) does: drop `buf`."""

    def __init__(self, answers):
        self.buf, self.answers = [], iter(answers)

    def readline(self):
        if self.buf:
            return self.buf.pop(0)
        try:
            return next(self.answers)
        except StopIteration:
            raise EOFError

    def flush(self):
        self.buf.clear()


def _type_ahead_session(flush):
    term, sent = FakeTerm(["y", "x"]), []

    def send(a, sr):
        sent.append(sr)
        term.buf.append("y")  # an impatient "y"+Enter typed while utterance 1 is still playing

    s = ConfirmSession(lambda ru: (np.ones(2, np.float32), 24000), send, out=lambda m: None,
                       readline=term.readline, flush=flush(term))
    return s, sent


def test_type_ahead_is_flushed_not_taken_as_answer():
    s, sent = _type_ahead_session(lambda term: term.flush)
    items = run(s, [Utterance(1, "a", "b"), Utterance(2, "c", "d")])
    assert [u.decision for u in items] == ["sent", "discarded"]  # utterance 2 got the user's real answer "x"
    assert sent == [24000]


def test_without_flush_type_ahead_would_send_unseen_utterance():
    # documents the bug the flush prevents: the stray "y" answers a prompt the user never saw
    s, sent = _type_ahead_session(lambda term: (lambda: None))
    items = run(s, [Utterance(1, "a", "b"), Utterance(2, "c", "d")])
    assert [u.decision for u in items] == ["sent", "sent"]


def test_flush_called_before_every_prompt_including_voice_retry():
    order = []
    keys = iter(["v1", "y", "x"])

    def rl():
        order.append("read")
        return next(keys)

    s = ConfirmSession(lambda ru: (np.ones(2, np.float32), 24000), lambda a, sr: None, out=lambda m: None,
                       readline=rl, set_voice=lambda n: None, flush=lambda: order.append("flush"))
    run(s, [Utterance(1, "a", "b"), Utterance(2, "c", "d")])
    assert order == ["flush", "read", "flush", "read", "flush", "read"]


def test_flush_input_posix_tty_uses_tcflush(monkeypatch):
    import termios

    from tinglongwu.reverse import confirm

    calls = []
    monkeypatch.setattr(confirm.sys, "platform", "linux")
    monkeypatch.setattr(termios, "tcflush", lambda fd, q: calls.append((fd, q)))
    tty = SimpleNamespace(fileno=lambda: 7, isatty=lambda: True)
    assert confirm.flush_input(tty) is True
    assert calls == [(7, termios.TCIFLUSH)]


def test_flush_input_windows_drains_msvcrt(monkeypatch):
    import sys as _sys

    from tinglongwu.reverse import confirm

    pending = ["\r", "\r", "y"]
    fake = SimpleNamespace(kbhit=lambda: bool(pending), getwch=lambda: pending.pop(0))
    monkeypatch.setitem(_sys.modules, "msvcrt", fake)
    monkeypatch.setattr(confirm.sys, "platform", "win32")
    tty = SimpleNamespace(fileno=lambda: 0, isatty=lambda: True)
    assert confirm.flush_input(tty) is True
    assert pending == []


def test_flush_input_non_tty_is_skipped_without_error(monkeypatch):
    import io
    import termios

    from tinglongwu.reverse import confirm

    monkeypatch.setattr(termios, "tcflush", lambda fd, q: (_ for _ in ()).throw(AssertionError("must not flush")))
    assert confirm.flush_input(SimpleNamespace(fileno=lambda: 3, isatty=lambda: False)) is False
    assert confirm.flush_input(io.StringIO("x\n")) is False  # fileno() raises UnsupportedOperation
    assert confirm.flush_input(object()) is False
