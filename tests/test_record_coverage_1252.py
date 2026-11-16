"""#1252 "every second of speech maps to some output line": the --record line spans (segments.jsonl start / end) that
the meeting replay (#1264) reads must hold all the audio ASR saw, at the position it really had in the input."""
import io
import json
import wave

import numpy as np

from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.merge import SentenceMerger
from tinglongwu.pipeline import run
from tinglongwu.record import Recorder
from tinglongwu.segment import Segmenter
from tinglongwu.sink import TextSink
from tinglongwu.translate import Translation, Translator

SR = 16000


class Frames:
    def __init__(self, audio, n=SR // 10):
        self.audio, self.n = audio, n

    def frames(self):
        for i in range(0, len(self.audio), self.n):
            yield self.audio[i:i + self.n]

    def close(self):
        pass


class OneSeg:  # every source frame is one segment
    def segments(self, frames):
        yield from frames


class SeqASR(Recognizer):
    def __init__(self, texts):
        self.it = iter(texts)

    def transcribe(self, audio):
        return Transcript(next(self.it), "en")


class Upper(Translator):
    def translate(self, text, src, tgt):
        return Translation(None, text.upper())


def _rows(tmp_path, source, segmenter, texts):
    rec = Recorder(tmp_path)
    run(source, segmenter, SeqASR(texts), Upper(), TextSink(io.StringIO()), "zh", SentenceMerger(max_wait=60),
        audio_clock=True, recorder=rec)
    rec.close()
    return [json.loads(x) for x in (tmp_path / "segments.jsonl").read_text("utf-8").splitlines()]


def _levels(tmp_path, name):
    with wave.open(str(tmp_path / name)) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2") / 32767.0
    return [round(float(pcm[i]), 1) for i in range(0, len(pcm), SR)]


def test_segment_whose_text_was_all_overlap_still_maps_to_a_line(tmp_path):
    # segment 2 only repeats the end of segment 1 (VAD overlap): the merger de-duplicates its whole text. Its audio
    # (1-2 s) used to belong to no line; it now goes into the next line, so the lines cover 0-4 s with no hole.
    audio = np.concatenate([np.full(SR, v, np.float32) for v in (0.1, 0.2, 0.3, 0.4)])
    rows = _rows(tmp_path, Frames(audio, SR), OneSeg(),
                 ["Hello there my good friend.", "good friend.", "Then we left the house.", "Bye now all of you."])
    assert [(r["start"], r["end"]) for r in rows] == [(0.0, 1.0), (1.0, 3.0), (3.0, 4.0)]
    assert [_levels(tmp_path, r["file"]) for r in rows] == [[0.1], [0.2, 0.3], [0.4]]


def test_a_segment_already_taken_is_not_repeated_in_the_next_line(tmp_path):
    audio = np.concatenate([np.full(SR, v, np.float32) for v in (0.1, 0.2)])
    rows = _rows(tmp_path, Frames(audio, SR), OneSeg(), ["Hello there my good friend.", "Then we left the house."])
    assert [(r["start"], r["end"]) for r in rows] == [(0.0, 1.0), (1.0, 2.0)]
    assert [_levels(tmp_path, r["file"]) for r in rows] == [[0.1], [0.2]]


def _nonzero_vad(buf):
    on = np.flatnonzero(np.abs(buf) > 0)
    return [(int(on[0]), int(on[-1]) + 1)] if len(on) else []


def test_pause_cut_line_span_is_where_its_audio_really_is(tmp_path):
    # 1 s silence, 1 s speech, 2 s silence. The pause cut yields 0.5 s before to 0.5 s after the speech (0.5-2.5 s)
    # once >= 0.6 s of silence was read; the line used to be stamped with the read position instead (shifted later).
    audio = np.concatenate([np.zeros(SR, np.float32), np.full(SR, 0.3, np.float32), np.zeros(2 * SR, np.float32)])
    seg = Segmenter(chunk=10.0, step=0.25, vad_fn=_nonzero_vad)
    [row] = _rows(tmp_path, Frames(audio), seg, ["One whole sentence here."])
    assert (row["start"], row["end"]) == (0.5, 2.5)
