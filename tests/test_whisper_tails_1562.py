"""#1562: the four late-tail outputs Whisper actually produced for the e2e #1395 clip are joined to the previous line.

These replace the live e2e #1395 step as the gate (that step is report-only now: 1 of 4 dev runs failed on a
different garbled tail each time). Each case: the head line, then the tail 1 s later in the audio but 9 s later on
the wall clock (slow CPU runner ASR), as in the e2e runs. Sources:
- "Version"     e2e on dev 6cb787b (en, no period)
- "И версии."   PR #1559 run 44361941709 (ru)
- "И версия."   ru variant listed in the #1562 freeze review (nominative)
- "Идер-си."    run 44399363350 on dev e1d1ced (ru, hyphenated garble)
"""
import pytest

from tinglongwu.merge import SentenceMerger

EN_HEAD = "Now I will show you the settings for the new."
RU_HEAD = "Сейчас я покажу вам настройки для новой."

CASES = [
    ("en", EN_HEAD, "Version", "Now I will show you the settings for the new version"),
    ("ru", RU_HEAD, "И версии.", "Сейчас я покажу вам настройки для новой и версии."),
    ("ru", RU_HEAD, "И версия.", "Сейчас я покажу вам настройки для новой и версия."),
    ("ru", RU_HEAD, "Идер-си.", "Сейчас я покажу вам настройки для новой идер-си."),
]


@pytest.mark.parametrize("lang,head,tail,merged", CASES, ids=[c[2] for c in CASES])
def test_whisper_tail_joins_previous_line_once(lang, head, tail, merged):
    m = SentenceMerger(min_words=3)
    first = m.add(head, lang, 0.0, span=(0.5, 3.6))
    assert [(u.text, u.replaces) for u in first] == [(head, None)]
    units = m.add(tail, lang, 9.0, span=(4.6, 5.3)) + m.due(20.0) + m.flush()
    # one replacement of the shown line, no one-word line of its own
    assert [(u.text, u.replaces) for u in units] == [(merged, head)]


@pytest.mark.parametrize("lang,head,tail,merged", CASES, ids=[c[2] for c in CASES])
def test_whisper_tail_after_a_long_audio_pause_stays_its_own_line(lang, head, tail, merged):
    # the join is only for a short pause: 4 s of silence in the audio keeps the tail separate
    m = SentenceMerger(min_words=3)
    m.add(head, lang, 0.0, span=(0.5, 3.6))
    units = m.add(tail, lang, 0.5, span=(7.6, 8.3)) + m.flush()
    assert all(u.replaces is None for u in units)
    assert merged not in [u.text for u in units]
