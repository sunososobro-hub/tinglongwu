"""#1255: silence hallucinations and repetition loops from the owner's ru meeting never reach the transcript.

Field run (2026-11-16): a "subscribe to our channel"-type line during silence, a number-repetition loop and
"ah ah ah" runs. Unit tests on the filters, plus a replay of segments through the real recognizer post-processing
and the pipeline's hallucination check (fake decoder, real tinglongwu code)."""
import re

import numpy as np
import pytest

from tinglongwu import asr
from tinglongwu.hallucination import is_hallucination
from tinglongwu.repeat import filter_hallucination

MAX_REPEATS = 6  # Acceptance: no fragment repeats more than 6 times


def max_run(text: str) -> int:
    """Longest run of one fragment (1-4 word/number tokens) repeated back to back."""
    toks = [t.lower() for t in re.findall(r"\w+", text)]
    best = 1 if toks else 0
    for n in range(1, 5):
        for i in range(len(toks) - n + 1):
            k = 1
            while toks[i + k * n:i + (k + 1) * n] == toks[i:i + n]:
                k += 1
            best = max(best, k)
    return best


@pytest.mark.parametrize("text", [
    "А-а-а-а-а-а-а-а-а-а", "Э-э-э-э-э-э-э-э", "а-а-а-а-а-а",
    "5,5,5,5,5,5,5,5,5,5", "2-2-2-2-2-2-2-2",
])
def test_one_letter_and_glued_number_loops_dropped(text):
    assert filter_hallucination(text) == ""


@pytest.mark.parametrize("text,want", [
    ("Ah-ah-ah-ah-ah-ah-ah-ah", "Ah"),
    ("10,10,10,10,10,10,10,10", "10"),
    ("Мы 1-1-1-1-1-1-1-1-1", "Мы 1"),
    ("И вот 15, 15, 15, 15, 15, 15, 15, 15, 15, 15", "И вот 15,"),
])
def test_number_loops_keep_one_copy(text, want):
    assert filter_hallucination(text) == want


@pytest.mark.parametrize("text", [
    "а-а-а", "Ну, а-а, это", "1.1.1.1", "1,000,000,000,000,000", "3.14", "Т-34", "д-р", "версия 2.0.1",
])
def test_real_speech_untouched(text):
    assert filter_hallucination(text) == text


@pytest.mark.parametrize("text", [
    "Подпишитесь на канал", "Подписывайтесь на наш канал!", "Не забудьте подписаться на канал.",
    "Ставьте лайки и подписывайтесь", "Субтитры создавались при поддержке",
])
def test_ru_channel_signoffs_dropped_at_any_energy(text):
    assert is_hallucination(text)
    assert is_hallucination(text, rms=0.2)


def test_en_subscribe_dropped_on_silence_kept_in_speech():
    assert is_hallucination("Subscribe to our channel", rms=0.005)
    assert not is_hallucination("Please subscribe to our channel for the quarterly numbers", rms=0.1)


class Seg:
    def __init__(self, text, no_speech=0.0):
        self.text, self.no_speech_prob, self.avg_logprob = text, no_speech, -0.3


class Decoder:
    """Fake faster-whisper: returns the scripted text for each call (a stuck decoder returns the loop again)."""

    def __init__(self, script):
        self.script = list(script)

    def transcribe(self, audio, **kw):
        text, ns = self.script.pop(0)
        return iter([Seg(text, ns)]), type("I", (), {"language": "ru"})()


# (seconds, rms, decoder output, no_speech_prob, retry output when the first pass loops)
REPLAY = [
    (6, 0.08, "Коллеги, давайте начнём с отчёта по второму кварталу.", 0.05, None),
    (8, 0.004, "Подписывайтесь на наш канал!", 0.6, None),
    (5, 0.07, "А-а-а-а-а-а-а-а-а-а-а-а-а-а", 0.2, "А-а-а-а-а-а-а-а-а-а-а-а"),
    (7, 0.09, "Выручка выросла на 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15 процентов", 0.05,
     "Выручка выросла на 15, 15, 15, 15, 15, 15, 15, 15, 15 процентов"),
    (9, 0.003, "Продолжение следует...", 0.7, None),
    (4, 0.06, "Э-э-э-э-э-э-э-э-э", 0.3, "Э-э-э-э-э-э-э"),
    (6, 0.08, "Сервер 10,10,10,10,10,10,10,10,10 отвечает", 0.1, "Сервер 10,10,10,10,10,10,10,10 отвечает"),
    (5, 0.005, "Спасибо за просмотр!", 0.5, None),
    (6, 0.08, "Итого рост на пятнадцать процентов, это хороший результат.", 0.05, None),
]
KNOWN = ["подпис", "продолжение следует", "спасибо за просмотр", "субтитры"]


def _replay():
    script = []
    for _s, _rms, text, ns, retry in REPLAY:
        script.append((text, ns))
        if retry is not None:
            script.append((retry, ns))
    r = asr.FasterWhisperRecognizer.__new__(asr.FasterWhisperRecognizer)
    r.model = Decoder(script)
    r.lang, r._last, r.glossary, r.live = "ru", "", None, False
    out = []
    for s, rms, *_ in REPLAY:
        audio = np.full(int(s * asr.SAMPLE_RATE), rms, np.float32)
        t = r.transcribe(audio)
        if t is None or is_hallucination(t.text, rms, t.no_speech):  # the pipeline's check (pipeline.py)
            continue
        out.append(t.text)
    return out


def test_replay_no_loops_and_no_known_hallucinations():
    out = _replay()
    assert out, "real speech must survive"
    assert all(max_run(line) <= MAX_REPEATS for line in out), out
    joined = " ".join(out).lower()
    assert not [k for k in KNOWN if k in joined], out
    assert "А-а" not in " ".join(out) and "Э-э" not in " ".join(out)
    assert any("второму кварталу" in line for line in out) and any("хороший результат" in line for line in out)
    assert any(line.startswith("Выручка выросла на 15") and "процентов" in line for line in out)
