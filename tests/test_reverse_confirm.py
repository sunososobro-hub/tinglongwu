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
                       lambda a, sr: prev.append(sr), out=log.append, readline=rl)
    return s, sent, prev, log, calls


def run(s, utts):
    for u in utts:
        s.submit(u)
    s.finish()
    return s.run()


def test_enter_sends_r_and_x_discard_in_order():
    s, sent, prev, log, calls = make(["", "r", "x", ""])
    items = run(s, [Utterance(i, f"z{i}", f"r{i}", f"b{i}") for i in range(1, 5)])
    assert [u.decision for u in items] == ["sent", "discarded", "discarded", "sent"]
    assert len(sent) == 2 and prev == [24000, 24000]
    shown = [m for m in log if "→ r" in m]
    assert [m.split("→ r")[1].split()[0] for m in shown] == ["1", "2", "3", "4"]
    assert " z1" in shown[0] and "→ r1" in shown[0] and "BACK(zh): b1" in shown[0]


def test_voice_switch_resynthesises_then_sends_new_voice():
    voice = {"v": 4}
    s, sent, prev, log, calls = make(["v1", "", "v9x", ""],
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
    s.readline = lambda: (gate.wait(5), "")[1]
    t = threading.Thread(target=lambda: run(s, [Utterance(1, "a", "b")]))
    t.start()
    time.sleep(0.3)
    assert sent == []  # synthesized in background, still unsent
    gate.set()
    t.join(5)
    assert len(sent) == 1


def test_queue_keeps_utterances_while_waiting():
    s, sent, *_ = make(["", "", ""])
    items = run(s, [Utterance(i, "z", "r") for i in range(3)])
    assert len(items) == 3 and len(sent) == 3


def test_unknown_key_and_eof_do_not_send():
    s, sent, *_ = make(["hello"])  # then EOF
    items = run(s, [Utterance(1, "a", "b"), Utterance(2, "c", "d")])
    assert sent == [] and [u.decision for u in items] == ["discarded", "discarded"]


def test_no_audio_and_no_preview():
    s, sent, prev, *_ = make([""], synth=lambda ru: None)
    items = run(s, [Utterance(1, "a", "b")])
    assert items[0].decision == "no_audio" and sent == [] and prev == []


def test_no_preview_device_ok():
    s, sent, *_ = make([""])
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
