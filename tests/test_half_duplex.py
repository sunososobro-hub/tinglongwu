import numpy as np

from tinglongwu.reverse.live import PlaybackGate, recognize_utterances


class _Asr:
    def transcribe(self, seg):
        from types import SimpleNamespace
        return SimpleNamespace(text="你好", no_speech=0.0)


def test_gate_busy_while_playing_and_during_tail():
    t = [0.0]
    g = PlaybackGate(tail=1.0, clock=lambda: t[0])
    assert not g.busy()
    g.start()
    assert g.busy()
    g.end()
    assert g.busy()      # tail
    t[0] = 1.5
    assert not g.busy()


def test_segments_dropped_while_tts_playing():
    loud = (np.ones(16000) * 0.1).astype(np.float32)  # 1 s: #249 drops segments shorter than gate.MIN_DUR_S
    g = PlaybackGate(tail=0.0)
    g.start()
    assert list(recognize_utterances([loud], _Asr(), gate=g)) == []
    g.end()
    assert list(recognize_utterances([loud], _Asr(), gate=g)) == ["你好"]
