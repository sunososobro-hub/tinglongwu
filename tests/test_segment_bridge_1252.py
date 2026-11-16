"""#1252 "every second of speech audio maps to some output line": audio between two VAD segments that the energy
check counts as voiced but Silero scores as non-speech (a soft last syllable) must not be dropped by the segmenter.
Meeting replay run 43911879772 had 0.6-0.7 s holes at the end of one FLEURS clip in every cycle."""
import numpy as np

from tinglongwu.segment import BRIDGE_S, Segmenter

SR = 16000
LOUD, SOFT = 0.5, 0.05


def _vad(buf):
    """Fake Silero: only loud samples are speech (the soft tail is not)."""
    on = np.flatnonzero(np.abs(buf) >= 0.2)
    if not len(on):
        return []
    spans, a, prev = [], on[0], on[0]
    for i in on[1:]:
        if i - prev > int(0.3 * SR):
            spans.append((int(a), int(prev) + 1))
            a = i
        prev = i
    spans.append((int(a), int(prev) + 1))
    return spans


def _audio(*parts):
    return np.concatenate([np.full(int(sec * SR), v, np.float32) for v, sec in parts])


def _spans(audio, seg):
    """(start s, end s) of every yielded segment on the input clock, stamped the way pipeline.run does it."""
    read = [0]

    def frames():
        n = SR // 10
        for i in range(0, len(audio), n):
            read[0] += len(audio[i:i + n])
            yield audio[i:i + n]

    out = []
    for s in seg.segments(frames()):
        end = (read[0] - seg.last_trail) / SR
        out.append((round(end - len(s) / SR, 3), round(end, 3)))
    return out


def _uncovered(spans, a, b):
    cur, miss = a, 0.0
    for s, e in sorted(spans):
        if e <= cur or s >= b:
            continue
        miss += max(0.0, s - cur)
        cur = max(cur, e)
    return round(miss + max(0.0, b - cur), 3)


def test_soft_tail_after_a_pause_cut_goes_into_the_next_segment():
    # silence 0.5 s, speech 1 s, soft tail 1 s (VAD: silence), 0.8 s silence, speech 1.5 s, silence 2 s
    audio = _audio((0.0, 0.5), (LOUD, 1.0), (SOFT, 1.0), (0.0, 0.8), (LOUD, 1.5), (0.0, 2.0))
    spans = _spans(audio, Segmenter(chunk=8.0, step=0.25, vad_fn=_vad))
    assert len(spans) == 2
    assert _uncovered(spans, 1.5, 2.5) == 0.0  # the soft tail is in a segment
    assert spans[1][0] == spans[0][1]  # the second segment starts where the first one ended
    assert _uncovered(spans, 0.5, 4.8) == 0.0  # all of the speech and the pause between


def test_long_silence_is_still_trimmed():
    # a silence longer than BRIDGE_S between two sentences is not sent to ASR
    audio = _audio((LOUD, 1.0), (0.0, BRIDGE_S + 2.0), (LOUD, 1.0), (0.0, 2.0))
    spans = _spans(audio, Segmenter(chunk=8.0, step=0.25, vad_fn=_vad))
    assert len(spans) == 2
    assert spans[1][1] - spans[1][0] <= 2.1  # 0.5 s before + 1 s speech + 0.5 s after (no bridged silence)


def test_forced_cut_still_bounds_the_segment_length():
    audio = _audio((LOUD, 1.0), (SOFT, 1.0), (0.0, 0.5), (LOUD, 12.0), (0.0, 2.0))
    spans = _spans(audio, Segmenter(chunk=8.0, step=0.25, vad_fn=_vad))
    assert max(e - s for s, e in spans) <= 8.0 + 0.25
    assert _uncovered(spans, 0.0, 14.5) == 0.0  # all speech (it ends at 14.5 s)


def test_record_line_spans_have_no_hole_through_the_pipeline(tmp_path):
    # the same input through pipeline.run with --record: the two lines' audio spans meet, the soft tail is in one
    import io
    import json

    from tinglongwu.asr import Recognizer, Transcript
    from tinglongwu.merge import SentenceMerger
    from tinglongwu.pipeline import run
    from tinglongwu.record import Recorder
    from tinglongwu.sink import TextSink
    from tinglongwu.translate import Translation, Translator

    class Frames:
        def frames(self):
            for i in range(0, len(audio), SR // 10):
                yield audio[i:i + SR // 10]

        def close(self):
            pass

    class SeqASR(Recognizer):
        it = iter(["Good morning to all of you.", "Then we left the house."])

        def transcribe(self, a):
            return Transcript(next(self.it), "en")

    class Upper(Translator):
        def translate(self, text, src, tgt):
            return Translation(None, text.upper())

    audio = _audio((0.0, 0.5), (LOUD, 1.0), (SOFT, 1.0), (0.0, 0.8), (LOUD, 1.5), (0.0, 2.0))
    rec = Recorder(tmp_path)
    run(Frames(), Segmenter(chunk=8.0, step=0.25, vad_fn=_vad), SeqASR(), Upper(), TextSink(io.StringIO()), "zh",
        SentenceMerger(max_wait=60), audio_clock=True, recorder=rec)
    rec.close()
    rows = [json.loads(x) for x in (tmp_path / "segments.jsonl").read_text("utf-8").splitlines()]
    assert len(rows) == 2
    assert _uncovered([(r["start"], r["end"]) for r in rows], 0.5, 4.8) == 0.0


def test_flush_at_end_of_input_does_not_bridge_a_long_silence():
    # CodeRabbit on #1318: speech 0-1.0 s and 4.6-4.7 s, 0.1 s frames, step 0.25: the second speech is only seen by
    # the end-of-input flush; its 3.1 s gap is longer than BRIDGE_S, so the last segment must not start at 1.5 s
    audio = _audio((LOUD, 1.0), (0.0, 3.6), (LOUD, 0.1))
    spans = _spans(audio, Segmenter(chunk=8.0, step=0.25, vad_fn=_vad))
    assert len(spans) == 2
    assert spans[1][0] == 4.1  # 0.5 s before the speech, not the previous segment's end (1.5 s)
