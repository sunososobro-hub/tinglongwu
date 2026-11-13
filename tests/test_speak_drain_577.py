"""#577: when the live input ends on its own (speak_selftest clip, --paced file), the queued TTS of the last sentence
must still be played. Since #427 cleanup() aborts the players, which dropped it: hw-tests recorded 85-89 % of TTS."""
import threading
from types import SimpleNamespace

import numpy as np

from tinglongwu.reverse import proto
from test_speak_ctrlc_427 import NoSpeech, _fake_soundcard


class ShortClip:
    """A source that ends by itself after a few blocks (like speak_selftest's ClipSource)."""
    def frames(self):
        for _ in range(3):
            yield np.zeros(1600, np.float32)

    def close(self):
        pass


def _run(monkeypatch, stop=None):
    monkeypatch.setattr(proto.sys, "platform", "linux")
    played = _fake_soundcard(monkeypatch)
    monkeypatch.setattr("tinglongwu.capture.mic.MicSource", lambda name: ShortClip())
    monkeypatch.setattr(proto, "make_segmenter", lambda *a, **k: (NoSpeech(), 0.5))
    player = proto.Player("tlwu_sink", 24000)

    def auto_live(items, *a, **k):
        for _ in range(3):  # the last sentence's TTS is still queued when the input ends
            player.push(np.zeros(4800, np.float32), 24000)
        if stop is not None:
            stop.set()  # the bar's Quit
        for _ in items:
            pass

    monkeypatch.setattr(proto, "_run_auto_live", auto_live)
    a = SimpleNamespace(mic_name="CABLE Input", chunk_s=10.0, to="en", ext_stop=stop)
    proto._live_main(a, None, None, SimpleNamespace(proc=None), player, SimpleNamespace(enabled=False), False,
                     {"items": []})
    return played, player


def test_input_end_plays_the_queued_tts_before_cleanup(monkeypatch):
    played, player = _run(monkeypatch)
    assert sum(played) == 3 * 4800  # nothing dropped
    assert not player.t.is_alive() and player.gate._n == 0


def test_quit_still_aborts_the_queued_tts(monkeypatch):
    played, player = _run(monkeypatch, stop=threading.Event())
    assert sum(played) < 3 * 4800  # #427 behaviour kept: Quit / Ctrl+C does not wait for the queue
    player.t.join(3)
    assert not player.t.is_alive()
