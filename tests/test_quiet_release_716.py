"""#716: after a forced chunk cut (or a fragment held by min_words), the silence that follows must release the held
text at the pause-send threshold instead of after the full max_wait."""
import numpy as np

from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.capture.base import SAMPLE_RATE as SR, AudioSource
from tinglongwu.merge import SentenceMerger
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.sink import Sink
from tinglongwu.translate import Translation, Translator


def energy_vad(audio, gap=0.3):
    """Speech = samples with |x| > 0.01; runs closer than `gap` s are joined (like Silero's min silence)."""
    idx = np.flatnonzero(np.abs(audio) > 0.01)
    if not len(idx):
        return []
    spans, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > gap * SR:
            spans.append((int(start), int(prev) + 1))
            start = i
        prev = i
    spans.append((int(start), int(prev) + 1))
    return spans


def audio(*parts):
    """parts: (seconds, speech?) -> float32 signal; speech is a 0.1 amplitude tone."""
    out = []
    for sec, speech in parts:
        n = int(sec * SR)
        out.append(0.1 * np.sin(np.arange(n) * 0.05).astype(np.float32) if speech else np.zeros(n, np.float32))
    return np.concatenate(out)


def frames(sig, block=0.1):
    n = int(block * SR)
    return [sig[i:i + n] for i in range(0, len(sig), n)]


def seg(chunk=8.0):
    return Segmenter(chunk=chunk, vad_fn=energy_vad, step=0.25, overlap=0.5, min_silence=0.6)


def test_forced_cut_then_silence_reports_quiet():
    # 7.9 s speech, 0.3 s silence (shorter than min_silence), then 3 s silence: the chunk limit cuts first
    s, quiet = seg(), []
    s.on_quiet = quiet.append
    out = []
    for x in s.segments(frames(audio((7.9, True), (0.3, False), (3.0, False)))):
        out.append((len(x) / SR, s.last_forced, s.last_pause))
    assert out and out[0][1] is True  # forced cut
    assert out[0][2] > 0.1  # the speech had already ended: its silence is reported, not 0
    # the silence after the last cut yields no segment: only on_quiet tells the consumer the speaker stopped
    assert quiet and quiet == sorted(quiet) and quiet[-1] >= 2.0


def test_no_quiet_while_speech_goes_on():
    s, quiet = seg(), []
    s.on_quiet = quiet.append
    list(s.segments(frames(audio((5.0, True)))))
    assert quiet == []  # no segment yet, nothing to release


def test_merger_quiet_releases_tail_after_forced_cut():
    m = SentenceMerger(max_wait=6.0, min_words=3)
    assert m.add("One of the biggest developments of that time", "en", 0.0, forced_cut=True) == []
    assert m.quiet(0.5, 0.5) == []  # shorter than the pause-send threshold (0.8)
    out = m.quiet(1.0, 1.0)
    assert [u.text for u in out] == ["One of the biggest developments of that time"]
    assert m.buf == ""


def test_merger_quiet_releases_held_fragment():
    m = SentenceMerger(max_wait=6.0, min_words=3)
    assert m.add("FTIR.", "en", 0.0, pause=0.9) == []  # fragment below min_words: held for a neighbour
    assert [u.text for u in m.quiet(1.2, 1.2)] == ["FTIR."]


def test_merger_quiet_keeps_short_head():
    m = SentenceMerger(max_wait=6.0)
    m.add("Well, in the", "en", 0.0, pause=0.9)
    assert m.quiet(1.5, 1.5) == []  # a 3-word head is not a sentence (#288): still waits


class Src(AudioSource):
    def __init__(self, sig):
        self.sig = sig

    def frames(self):
        yield from frames(self.sig)

    def close(self):
        pass


class ByLength(Recognizer):
    """Long (forced) segment -> unfinished sentence; short overlap tail -> nothing; later speech -> new sentence."""

    def __init__(self):
        self.n = 0

    def transcribe(self, a):
        if len(a) / SR < 2.5:  # the overlap tail after the forced cut (~1.9 s)
            return None
        self.n += 1
        return Transcript("One of the biggest developments of that time" if self.n == 1 else "It changed everything.",
                          "en")


class Echo(Translator):
    def translate(self, text, src, tgt):
        return Translation(None, text)


class Lines(Sink):
    def __init__(self):
        self.lines = []

    def write(self, r):
        self.lines.append(r.original)


def test_pipeline_releases_held_text_at_pause_not_max_wait():
    sig = audio((9.0, True), (3.0, False), (2.0, True), (1.0, False))
    sink = Lines()
    run(Src(sig), seg(), ByLength(), Echo(), sink, tgt="zh", merger=SentenceMerger(max_wait=100.0, min_words=3),
        audio_clock=True)
    # before #716 the held head waited for max_wait and was glued to the next sentence 3 s later
    assert sink.lines == ["One of the biggest developments of that time", "It changed everything."]
