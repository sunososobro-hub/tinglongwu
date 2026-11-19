"""#1562: the late-fragment window (#1395) is the pause in the audio, not the wall time between two ASR results.

On the CPU e2e runner a 1 s spoken pause reached the merger 9 s after the line (ASR / queue delay), so "Version"
stayed a line of its own; Whisper also heard the Russian tail as "И версии." (capitalised 2-word clause).
Example sentences are made up (generic software talk)."""
import time

import numpy as np

from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.merge import SentenceMerger
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.sink import Sink
from pivot_fake import PivotTranslator


def _m(**kw):
    return SentenceMerger(min_words=3, **kw)


def _texts(units):
    return [(u.text, u.replaces) for u in units]


HEAD = "Now I will show you the settings for the new."


def test_slow_asr_tail_still_joins_when_the_audio_pause_was_short():
    m = _m()
    assert _texts(m.add(HEAD, "en", 0.0, span=(0.5, 3.6))) == [(HEAD, None)]
    out = m.add("Version.", "en", 9.0, span=(4.6, 5.3))  # 9 s later on the wall, 1 s after the line in the audio
    assert _texts(out) == [("Now I will show you the settings for the new version.", HEAD)]


def test_unfinished_tail_released_later_uses_its_audio_start():
    m = _m(max_wait=1.0)
    m.add(HEAD, "en", 0.0, span=(0.5, 3.6))
    assert m.add("version", "en", 9.0, span=(4.6, 5.3)) == []  # no period: waits for more text as before
    assert _texts(m.due(10.0)) == [("Now I will show you the settings for the new version", HEAD)]


def test_long_audio_pause_stays_separate_even_when_asr_is_quick():
    m = _m()
    m.add(HEAD, "en", 0.0, span=(0.5, 3.6))
    assert m.add("Version.", "en", 0.5, span=(7.0, 7.6)) == []  # 0.5 s on the wall, 3.4 s of silence in the audio
    assert _texts(m.flush()) == [("Version.", None)]


def test_empty_segment_does_not_move_the_released_line_end():
    m = _m(max_wait=1.0)
    assert m.add("Now I will show you the settings", "en", 0.0, span=(0.0, 2.0)) == []
    assert m.add("", "en", 0.5, span=(10.0, 11.0)) == []  # no text: not part of the buffered line
    assert _texts(m.due(1.0)) == [("Now I will show you the settings", None)]
    assert m.add("Version.", "en", 1.5, span=(12.0, 13.0)) == []  # 10 s after the line in the audio: own line
    assert _texts(m.flush()) == [("Version.", None)]


def test_whole_mode_clause_release_falls_back_to_the_wall_clock():
    m = SentenceMerger(min_words=3, max_wait=1.0, whole=True)
    assert m.add("We should open the settings, of the", "en", 0.0, span=(0.0, 2.0)) == []
    assert m.add("new", "en", 0.2, span=(10.0, 11.0)) == []
    assert _texts(m.due(1.0)) == [("We should open the settings,", None)]  # the clause; "of the new" stays
    assert m._last[3] is None  # its audio end is unknown: not the newest segment's 11.0


def test_sentence_and_clause_in_one_release_clause_has_no_audio_end():
    m = SentenceMerger(min_words=3, max_words=6, whole=True)
    out = m.add("We should open the settings. We should save the changes, vers", "en", 0.0, span=(0.0, 11.0))
    assert _texts(out) == [("We should open the settings.", None), ("We should save the changes,", None)]
    assert m._last[3] is None  # the clause (last released) has no audio end, the sentence did not take the marker


def test_without_spans_the_wall_clock_window_is_unchanged():
    m = _m()
    m.add(HEAD, "en", 0.0)
    assert m.add("Version.", "en", 9.0) == []


def test_conjunction_led_tail_continues_the_sentence():
    m = _m()
    prev = "Сейчас я покажу вам настройки для новой."
    m.add(prev, "ru", 0.0, span=(0.5, 3.0))
    out = m.add("И версии.", "ru", 2.0, span=(4.0, 4.8))
    assert _texts(out) == [("Сейчас я покажу вам настройки для новой и версии.", prev)]
    m = _m()
    m.add(HEAD, "en", 0.0)
    assert _texts(m.add("And version.", "en", 1.0)) == [
        ("Now I will show you the settings for the new and version.", HEAD)]


def test_other_capitalised_two_word_clause_still_separate():
    m = _m()
    m.add("We finished the migration.", "en", 0.0, span=(0.0, 2.0))
    assert m.add("Thank you.", "en", 1.0, span=(2.5, 3.2)) == []
    assert _texts(m.flush()) == [("Thank you.", None)]


# ---- live pipeline: wall-clock ASR delay longer than the window, contiguous audio ----
class _SlowASR(Recognizer):
    def __init__(self, items, delays):
        self.items, self.delays = list(items), list(delays)

    def transcribe(self, audio):
        time.sleep(self.delays.pop(0))
        return Transcript(self.items.pop(0), "en")


class _Src:
    def frames(self):
        for _ in range(2):
            yield np.ones(16000, np.float32) * 0.3

    def close(self):
        pass


class _Collect(Sink):
    def __init__(self):
        self.rows = []

    def write(self, r):
        self.rows.append(r)


def test_live_run_joins_a_tail_whose_asr_result_came_late(tmp_path):
    out = _Collect()
    tr = PivotTranslator(tmp_path, "tw", lambda s, t: (lambda x: x))
    # window 0.3 s; the tail's ASR call takes 0.6 s, its audio follows the line's directly (no pause)
    run(_Src(), Segmenter(chunk=1.0, use_vad=False), _SlowASR([HEAD, "Version."], [0.0, 0.6]), tr, out,
        tgt="zh", merger=SentenceMerger(min_words=3, amend_window=0.3), audio_clock=False)
    assert [(r.original, r.replaces) for r in out.rows][-1] == (
        "Now I will show you the settings for the new version.", HEAD)


RU_HEAD = "Сейчас я покажу вам настройки для новой."


def test_garbled_hyphenated_ru_tail_joins_the_previous_line():
    # e2e run 44399363350 (dev e1d1ced): Whisper heard "и версии" as "Идер-си." 1 s after the line; one hyphenated
    # word, not a capitalised 2-word clause
    m = _m()
    m.add(RU_HEAD, "ru", 0.0, span=(0.5, 3.6))
    out = m.add("Идер-си.", "ru", 9.0, span=(4.6, 5.3))
    assert _texts(out) == [("Сейчас я покажу вам настройки для новой идер-си.", RU_HEAD)]


def test_capitalised_two_word_clause_still_stays_separate():
    m = _m()
    m.add(RU_HEAD, "ru", 0.0, span=(0.5, 3.6))
    assert m.add("Вот так.", "ru", 0.5, span=(4.6, 5.3)) == []
    assert _texts(m.flush()) == [("Вот так.", None)]
