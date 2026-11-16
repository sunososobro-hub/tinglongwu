"""#1193 (user report): --preview-device also plays in --no-confirm live mode, and the opt-in --enter-sends
confirm prompt (bare Enter sends, y sends + switches to auto-send, anything else discards)."""
from types import SimpleNamespace

import numpy as np

from tinglongwu.reverse import args as rargs
from tinglongwu.reverse import proto
from tinglongwu.reverse.confirm import ConfirmSession, Utterance
from tinglongwu.reverse.session import preview_notes


class MT:
    history: list = []

    def translate(self, text, src, tgt):
        return SimpleNamespace(text="Hello there. Second one.")


class TTS:
    engine = "none"

    def speak(self, text):
        yield {"t": "chunk", "sr": 24000}, np.ones(4, np.float32).tobytes()
        yield {"t": "done"}, None


class Player:
    def __init__(self, enabled=True):
        self.enabled, self.pushed = enabled, []

    def push(self, audio, sr):
        self.pushed.append((len(audio), sr))


def test_auto_live_pushes_every_piece_to_player_and_enabled_previewer():
    player, prev = Player(), Player()
    res = {"items": []}
    proto._run_auto_live(iter([(1, "mic", "你好"), (2, "mic", "再見")]), MT(), TTS(), player, res, "en",
                         previewer=prev)
    assert player.pushed and prev.pushed == player.pushed
    assert [it["decision"] for it in res["items"]] == ["sent", "sent"]


def test_auto_live_skips_disabled_or_missing_previewer():
    for prev in (Player(enabled=False), None):
        player = Player()
        proto._run_auto_live(iter([(1, "mic", "你好")]), MT(), TTS(), player, {"items": []}, "en", previewer=prev)
        assert player.pushed
        assert prev is None or prev.pushed == []


def test_preview_notes():
    assert preview_notes(None, "CABLE Input") == []
    same = preview_notes("CABLE Input", "cable input")
    assert len(same) == 1 and same[0].startswith("warning:") and "same as --device" in same[0]
    other = preview_notes("Headphones", "CABLE Input")
    assert len(other) == 1 and "echo cancellation" in other[0]


def make(keys, enter_sends=True, mode="confirm"):
    sent, log, flushes = [], [], []
    it = iter(keys)

    def rl():
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    s = ConfirmSession(lambda ru: (np.ones(4, np.float32), 24000), lambda a, sr: sent.append(sr), out=log.append,
                       readline=rl, flush=lambda: flushes.append(1), max_pending=100, mode=mode,
                       enter_sends=enter_sends)
    return s, sent, log, flushes


def run(s, utts):
    for u in utts:
        s.submit(u)
    s.finish()
    return s.run()


def test_enter_sends_bare_enter_sends_other_keys_discard():
    s, sent, log, flushes = make(["", "x", "r", "  "])
    items = run(s, [Utterance(i, "你好", "Hello") for i in range(4)])
    assert [u.decision for u in items] == ["sent", "discarded", "discarded", "sent"]
    assert len(sent) == 2 and s.mode == "confirm"
    assert any("[Enter]=send" in m for m in log)
    assert len(flushes) >= 4  # #248 type-ahead flush still runs before every prompt


def test_y_sends_and_switches_to_auto_send():
    s, sent, log, _ = make(["y"])
    u2 = Utterance(2, "再見", "Bye")
    u2.back = "再見"  # a confident line: sent at once in auto mode, no prompt
    items = run(s, [Utterance(1, "你好", "Hello"), u2])
    assert [u.decision for u in items] == ["sent", "sent"] and s.mode == "auto"
    assert any("auto-send on" in m for m in log)


def test_low_confidence_line_still_needs_y_with_enter_sends():
    s, sent, log, _ = make(["", "y"])
    a, b = Utterance(1, "你好", "Hello"), Utterance(2, "你好", "Hello")
    a.extra["halluc"] = True
    b.extra["halluc"] = True
    items = run(s, [a, b])
    assert [u.decision for u in items] == ["discarded", "sent"]
    assert not any("[Enter]=send" in m for m in log)


def test_default_is_unchanged_bare_enter_discards(monkeypatch):
    monkeypatch.delenv("TLWU_ENTER_SENDS", raising=False)
    s, sent, _, _ = make(["", "y"], enter_sends=None)
    items = run(s, [Utterance(1, "你好", "Hello"), Utterance(2, "你好", "Hello")])
    assert [u.decision for u in items] == ["discarded", "sent"] and s.mode == "confirm"


def test_env_turns_enter_sends_on(monkeypatch):
    monkeypatch.setenv("TLWU_ENTER_SENDS", "1")
    s, _, _, _ = make([""], enter_sends=None)
    assert [u.decision for u in run(s, [Utterance(1, "你好", "Hello")])] == ["sent"]


def test_cli_flag_reaches_confirm_session(monkeypatch):
    a = rargs.build_parser().parse_args(["--mic", "x", "--enter-sends"])
    assert a.enter_sends is True
    monkeypatch.setattr("builtins.input", lambda *x: "")
    player = Player()
    res = {"items": []}
    proto._run_confirm(SimpleNamespace(to="en", enter_sends=True, no_back=True), iter([(1, "mic", "你好")]), MT(),
                       TTS(), player, Player(enabled=False), res)
    assert res["items"][0]["decision"] == "sent" and player.pushed
