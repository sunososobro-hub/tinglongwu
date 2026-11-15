"""#1052: one spoken sentence made 3 bubbles. The GUI speak path (listen pipeline + voice) had no half-duplex gate, so
the voice played on the speakers was heard by the mic, transcribed, translated and spoken again (the CLI speak path
has had PlaybackGate since #249). The speak leg now feeds its capture silence while the voice is queued / playing."""
import threading
import time

import numpy as np

from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.capture.base import AnyOf, MuteSource
from tinglongwu.gui.core import GUI_SEND_MODES, SpeakQueueSink, load_settings
from tinglongwu.pipeline import run
from tinglongwu.translate import Translation, Translator
from tinglongwu.voice import VoiceSink


class Voice:
    """speak() that 'plays' for a while; `playing` is what the room (and so the mic) hears."""

    def __init__(self, dur=0.15):
        self.playing, self.spoken, self.dur = threading.Event(), [], dur

    def __call__(self, text):
        self.spoken.append(text)
        self.playing.set()
        time.sleep(self.dur)
        self.playing.clear()


def test_voice_sink_is_busy_from_queue_until_spoken_plus_tail():
    v = Voice()
    sink = VoiceSink(SpeakQueueSink.__new__(SpeakQueueSink), v)
    sink.playback.tail = 0.0
    assert not sink.playback.busy()
    sink.enqueue("hello")
    assert sink.playback.busy()  # queued already counts: the gap before playback must not leak either
    assert sink.close(timeout=5)
    assert v.spoken == ["hello"] and not sink.playback.busy()


def test_dropped_lines_release_the_gate():
    sink = VoiceSink(SpeakQueueSink.__new__(SpeakQueueSink), lambda t: time.sleep(0.2), max_backlog=1)
    sink.playback.tail = 0.0
    for t in ("a", "b", "c"):
        sink.enqueue(t)
    assert sink.close(timeout=5)
    assert not sink.playback.busy()  # no counter leak: the mic would stay muted for good


def test_one_utterance_is_sent_and_shown_exactly_once_even_when_the_mic_hears_the_voice():
    voice = Voice()
    n = [0]

    class Asr(Recognizer):
        def transcribe(self, audio):  # distinct text per heard segment (identical finals collapse, #318)
            n[0] += 1
            return Transcript(f"你聽得到嗎 {n[0]}", "zh")

    class Mt(Translator):
        def translate(self, text, src, tgt):
            return Translation(None, f"Can you hear it? {text[-1]}")

    q = __import__("queue").Queue()
    sink = VoiceSink(SpeakQueueSink(q), voice)
    sink.playback.tail = 0.3

    class Room:  # the microphone: the user's sentence first, then whatever the speakers play
        def frames(self):
            yield np.full(16000, 0.5, dtype="float32")  # the one utterance
            for _ in range(60):
                time.sleep(0.01)
                yield np.full(16000, 0.5 if voice.playing.is_set() else 0.0, dtype="float32")

        def close(self):
            pass

    class EachFrame:
        def segments(self, frames):
            yield from frames

    src = MuteSource(Room(), AnyOf(sink.playback.busy))
    run(src, EachFrame(), Asr(), Mt(), sink, "en", None, audio_clock=True)
    assert sink.close(timeout=5)
    finals = []
    while not q.empty():
        finals.append(q.get())
    assert [f[0] for f in finals] == ["final_side"]  # exactly one bubble
    assert len(voice.spoken) == 1  # exactly one send


def test_gui_send_default_is_direct_and_countdown_is_not_offered(tmp_path):
    assert GUI_SEND_MODES == ("auto", "confirm")
    assert load_settings(tmp_path / "none.json")["send_mode"] == "auto"
    p = tmp_path / "s.json"
    p.write_text('{"send_mode": "countdown"}', "utf-8")
    assert load_settings(p)["send_mode"] == "auto"
    p.write_text('{"send_mode": "confirm"}', "utf-8")
    assert load_settings(p)["send_mode"] == "confirm"  # an explicit hold-first choice stays
