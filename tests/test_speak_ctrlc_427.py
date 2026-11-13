"""#427: Ctrl+C in live speak mode must leave no thread inside the audio backend (Linux: PulseAudio), otherwise the
interpreter shuts down under it and the process hangs after 'peak VRAM' or dies with SIGABRT."""
import _thread
import sys
import threading
import time
import types
from types import SimpleNamespace

import numpy as np

from tinglongwu.reverse import proto
from tinglongwu.reverse.live import capture_segments


class SilentMic:
    """Like capture._soundcard.SoundcardSource: yields 100 ms blocks until close() is called (a room in silence)."""
    def __init__(self):
        self.closed = threading.Event()
        self.released = threading.Event()  # the recorder `with` was left

    def frames(self):
        try:
            while not self.closed.is_set():
                time.sleep(0.01)
                yield np.zeros(1600, np.float32)
        finally:
            self.released.set()

    def close(self):
        self.closed.set()


class NoSpeech:
    """VAD that never finds speech: the capture thread never hands anything to the consumer."""
    def segments(self, frames):
        yield from (f for f in frames if False)


def _capture_threads():
    return [t for t in threading.enumerate() if t.name == "tlwu-speak-capture"]


def test_stop_closes_the_source_and_ends_the_capture_thread():
    mic, stop = SilentMic(), threading.Event()
    threading.Timer(0.3, stop.set).start()
    assert list(capture_segments(mic, NoSpeech(), stop)) == []
    assert mic.closed.is_set() and mic.released.is_set()
    assert not _capture_threads()


def test_closing_the_generator_mid_wait_releases_the_recorder():
    """Ctrl+C lands while the consumer is busy (MT/TTS): the generator is closed, not exhausted."""
    mic = SilentMic()

    class OneSegment:
        def segments(self, frames):
            for i, _ in enumerate(frames):
                if i == 2:
                    yield np.zeros(16000, np.float32)

    g = capture_segments(mic, OneSegment(), threading.Event())
    assert next(g).shape == (16000,)
    g.close()
    assert mic.closed.is_set() and mic.released.wait(2)
    assert not _capture_threads()


def test_ctrl_c_in_live_main_closes_mic_and_aborts_players(monkeypatch):
    mic = SilentMic()
    monkeypatch.setattr("tinglongwu.capture.mic.MicSource", lambda name: mic)
    monkeypatch.setattr(proto, "make_segmenter", lambda *a, **k: (NoSpeech(), 0.5))

    def interrupted(items, *a, **k):
        threading.Timer(0.3, _thread.interrupt_main).start()  # Ctrl+C while waiting for speech (main thread)
        for _ in items:
            pass

    monkeypatch.setattr(proto, "_run_auto_live", interrupted)

    class Pl:
        enabled = True
        aborted = False

        def abort(self):
            self.aborted, self.enabled = True, False

    killed = threading.Event()
    tts = SimpleNamespace(proc=SimpleNamespace(kill=killed.set))
    player, previewer = Pl(), Pl()
    a = SimpleNamespace(mic_name="Monitor of E2E-Speaker", chunk_s=10.0, to="en")
    proto._live_main(a, None, None, tts, player, previewer, False, {"items": []})
    assert not _capture_threads()  # joined before _live_main returned (not merely told to stop)
    assert mic.closed.is_set() and mic.released.is_set()
    assert killed.is_set() and player.aborted and previewer.aborted


def test_ctrl_c_in_confirm_mode_does_not_raise_and_releases_the_mic(monkeypatch):
    """Default confirm mode: a producer thread is inside the utterance generator when Ctrl+C reaches the main
    thread (sess.run), so closing that generator from the main thread raised 'generator already executing'."""
    mic = SilentMic()
    monkeypatch.setattr("tinglongwu.capture.mic.MicSource", lambda name: mic)
    monkeypatch.setattr(proto, "make_segmenter", lambda *a, **k: (NoSpeech(), 0.5))

    def confirm(a, items, *rest):
        threading.Thread(target=lambda: [None for _ in items], daemon=True).start()  # the producer
        threading.Timer(0.3, _thread.interrupt_main).start()
        time.sleep(5)  # sess.run() waiting for an answer

    monkeypatch.setattr(proto, "_run_confirm", confirm)
    pl = SimpleNamespace(enabled=True, abort=lambda: None)
    a = SimpleNamespace(mic_name="Monitor of E2E-Speaker", chunk_s=10.0, to="en")
    proto._live_main(a, None, None, SimpleNamespace(proc=None), pl, pl, True, {"items": []})
    assert mic.closed.is_set() and mic.released.is_set()
    assert not _capture_threads()


def _fake_soundcard(monkeypatch, play_s=None):
    played = []

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def play(self, data):
            time.sleep(play_s if play_s is not None else len(data) / 24000)  # real time unless play_s is given
            played.append(len(data))

    spk = types.SimpleNamespace(name="tlwu_sink", player=lambda **kw: Stream())
    monkeypatch.setitem(sys.modules, "soundcard",
                        types.SimpleNamespace(all_speakers=lambda: [spk], default_speaker=lambda: spk))
    return played


def test_player_abort_drops_queued_audio_and_ends_the_playback_thread(monkeypatch):
    monkeypatch.setattr(proto.sys, "platform", "linux")
    played = _fake_soundcard(monkeypatch)
    p = proto.Player("tlwu_sink", 24000)
    for _ in range(3):
        p.push(np.zeros(24000 * 8, np.float32), 24000)  # 8 s pieces: longer than any join timeout
    time.sleep(0.3)  # the first piece is playing
    t0 = time.monotonic()
    p.abort()
    p.close()
    assert not p.t.is_alive() and time.monotonic() - t0 < 1
    assert sum(played) < 24000  # under 1 s of the 8 s piece was written; the queued pieces were dropped
    assert p.gate._n == 0  # every pushed piece released the half-duplex gate


def test_player_close_still_plays_everything_queued(monkeypatch):
    monkeypatch.setattr(proto.sys, "platform", "linux")
    played = _fake_soundcard(monkeypatch)
    p = proto.Player("tlwu_sink", 24000)
    for _ in range(3):
        p.push(np.zeros(3600, np.float32), 24000)
    p.close()
    assert sum(played) == 3 * 3600 and not p.t.is_alive()
