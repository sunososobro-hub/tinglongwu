"""#968: GUI speak (listen pipeline + voice + SendGate) runs the #249 speak input gate on every segment.

Keyboard noise that Whisper turns into an invented sentence (low logprob / high no_speech) is never sent and is
shown greyed as "not sent: unclear"; a clear line is still sent at once in auto mode."""
import numpy as np

from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.capture.base import AudioSource
from tinglongwu.gui.core import send_gate_line
from tinglongwu.pipeline import run
from tinglongwu.reverse import gate as G
from tinglongwu.reverse.sendgate import REJECT_SHOW_S, GateView, SendGate
from tinglongwu.segment import Segmenter
from tinglongwu.sink import Sink
from tinglongwu.translate import Translation, Translator
from tinglongwu.voice import VoiceSink

SR = 16000
# What Whisper returns on keyboard typing in the owner's test (#968): fluent text, but unsure (stats are the
# values gate.py documents as Whisper's own limits, one on each side of them).
NOISE = [Transcript("今天是開心的一天", "zh", no_speech=0.82, avg_logprob=-0.4),
         Transcript("人們希望我們邁出一步", "zh", no_speech=0.1, avg_logprob=-1.35)]
SPEECH = Transcript("你好，你聽得到我嗎", "zh", no_speech=0.05, avg_logprob=-0.3)


class Chunks(AudioSource):
    """One 1 s chunk of audible noise per scripted transcript (the segmenter cuts every 1 s)."""

    def __init__(self, n):
        self.n = n

    def frames(self):
        rng = np.random.default_rng(0)
        for _ in range(self.n):
            yield (0.05 * rng.standard_normal(SR)).astype(np.float32)


class Scripted(Recognizer):
    def __init__(self, items):
        self.items = list(items)

    def transcribe(self, audio):
        return self.items.pop(0)


class EchoMT(Translator):
    def translate(self, text, src, tgt):
        return Translation(None, f"EN<{text}>")


class Null(Sink):
    def write(self, r):
        pass


def _speak(items, mode="auto", close=True):
    spoken, clock = [], [100.0]
    gate = SendGate(lambda t: None, lambda: clock[0], mode=mode)
    sink = VoiceSink(Null(), spoken.append)
    gate.release, sink.gate = sink.enqueue, gate  # what voice.open_voice does for the GUI
    run(Chunks(len(items)), Segmenter(chunk=1.0, use_vad=False), Scripted(items), EchoMT(), sink, "en",
        audio_clock=True, seg_gate=G.segment_gate(gate.reject, report=G.DropReport(out=lambda m: None)))
    if close:
        sink.close()  # session end: a held or rejected line is cleared (never sent after Stop)
    return spoken, gate, clock


def test_speak_noise_gate_drops_noise_and_sends_speech_in_auto_mode():
    spoken, gate, _ = _speak(NOISE + [SPEECH])
    assert spoken == [f"EN<{SPEECH.text}>"]
    assert gate.rejected == 2 and gate.sent == 1


def test_speak_noise_gate_rejected_line_is_shown_greyed_then_clears():
    _, gate, clock = _speak(NOISE, close=False)
    v = gate.view()
    assert v.state == "rejected" and v.text == NOISE[1].text and v.reason == "low_logprob"
    line = send_gate_line(v)
    assert line.startswith("Not sent: unclear (low_logprob)") and NOISE[1].text in line
    clock[0] += REJECT_SHOW_S + 0.1
    assert gate.view().state == "idle"
    gate.reject("x", "no_speech")
    gate.clear()
    assert gate.view().state == "idle"


def test_speak_noise_gate_reasons_match_whisper_limits():
    seen = []
    sg = G.segment_gate(lambda t, r: seen.append(r), report=G.DropReport(out=lambda m: None))
    seg = np.full(SR, 0.05, np.float32)
    assert [sg(seg, t) for t in NOISE] == [False, False]
    assert seen == ["no_speech", "low_logprob"]
    assert sg(seg, SPEECH) is True
    assert sg(np.zeros(SR, np.float32), SPEECH) is False and len(seen) == 2  # silence: dropped, not shown


def test_speak_noise_gate_without_seg_gate_pipeline_is_unchanged():
    spoken, clock = [], [100.0]
    gate = SendGate(lambda t: None, lambda: clock[0], mode="auto")
    sink = VoiceSink(Null(), spoken.append)
    gate.release, sink.gate = sink.enqueue, gate
    run(Chunks(1), Segmenter(chunk=1.0, use_vad=False), Scripted(NOISE[:1]), EchoMT(), sink, "en",
        audio_clock=True)
    sink.close()
    assert spoken == [f"EN<{NOISE[0].text}>"]  # listen pipeline alone keeps lines (listen mode is out of scope)


def test_speak_noise_gate_held_line_says_why():
    assert send_gate_line(GateView("hello", "asking", "hallucination flag")).endswith("[hallucination flag] hello")
    assert send_gate_line(GateView("hello", "asking", "")).endswith(": hello")
