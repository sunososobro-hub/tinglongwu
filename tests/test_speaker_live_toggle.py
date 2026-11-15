"""0.3.8: the 標示說話者 toggle applies live: LiveSpeakers attaches / detaches a labeler under a running pipeline."""
import threading
import time

import numpy as np

from tinglongwu.speaker_embed import AsyncSpeakerLabeler, LiveSpeakers

SEG = np.ones(32000, np.float32)


def _lab():
    return AsyncSpeakerLabeler(lambda seg: [1.0, 0.0])


def _wait(cond, secs=3.0):
    t0 = time.monotonic()
    while not cond() and time.monotonic() - t0 < secs:
        time.sleep(0.01)
    return cond()


def test_off_then_on_midrun_labels_only_later_results():
    live = LiveSpeakers(None)
    tok0, _ = live.label(SEG)  # no labeler yet: unknown, nothing queued
    assert live.result(tok0) is None
    lab = _lab()
    live.set(lab)
    tok, _ = live.label(SEG)
    assert tok.startswith("spk#") and _wait(lambda: live.result(tok) == "說話者1")
    assert live.result(tok0) is None  # the earlier bubble stays unlabelled
    live.close()


def test_off_midrun_closes_labeler_and_gives_no_labels():
    lab = _lab()
    live = LiveSpeakers(lab)
    old, _ = live.label(SEG)
    assert _wait(lambda: live.result(old) is not None)
    live.set(None)
    assert lab._closed is True
    assert live.label(SEG)[0] is not None and live.get() is None
    new, _ = live.label(SEG)
    assert live.result(new) is None  # no label for new bubbles
    assert live.result(old) == "說話者1"  # existing labels stay readable
    live.close()


def test_tokens_unique_across_labelers_and_closed_session_rejects_attach():
    a, b = _lab(), _lab()
    live = LiveSpeakers(a)
    t1, _ = live.label(SEG)
    live.set(b)
    t2, _ = live.label(SEG)
    assert t1 != t2
    live.close()
    c = _lab()
    live.set(c)  # session over: the late labeler is closed at once, never attached
    assert c._closed and live.get() is None


def test_swap_during_processing_never_raises():
    live = LiveSpeakers(_lab())
    errs, stop = [], threading.Event()

    def pipeline():
        try:
            while not stop.is_set():
                live.label(SEG)
                live.result("spk#1")
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    th = threading.Thread(target=pipeline)
    th.start()
    for _ in range(30):
        live.set(_lab())
        live.set(None)
    stop.set()
    th.join(3)
    live.close()
    assert not errs and not th.is_alive()
