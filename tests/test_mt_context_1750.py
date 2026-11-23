"""#1750 --mt-context: the previous finished source line of the same speaker and language reaches the translator as
context; off by default. Example sentences are made up (generic software talk)."""
import numpy as np

from tinglongwu.adapt import Ladder, SwitchableTranslator
from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.merge import SentenceMerger
from tinglongwu.mt_context import CONTEXT_MAX_PAUSE_S, LineContext, last_sentence
from tinglongwu.mt_pending import PendingTranslator
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.sink import Sink
from tinglongwu.translate import Translation, Translator, translate_checked


class _Ctx(Translator):
    """Records every call; takes context= like Hy-MT2 / JaRouter."""
    takes_context = True

    def __init__(self):
        self.calls = []

    def translate(self, text, src, tgt, draft=False, context=None):
        self.calls.append((text, src, context))
        return Translation(None, f"[{text}]")


class _Plain(Translator):
    """An engine without context support (FM, Ollama): context= would be a TypeError."""

    def __init__(self):
        self.calls = []

    def translate(self, text, src, tgt):
        self.calls.append(text)
        return Translation(None, text)


class _ASR(Recognizer):
    def __init__(self, items):
        self.items = list(items)

    def transcribe(self, audio):
        text, lang = self.items.pop(0)
        return Transcript(text, lang)


class _Src:
    def __init__(self, n):
        self.n = n

    def frames(self):
        for _ in range(self.n):
            yield np.ones(16000, np.float32) * 0.3

    def close(self):
        pass


class _Collect(Sink):
    def __init__(self):
        self.rows = []

    def write(self, r):
        self.rows.append(r)


def _run(items, **kw):
    tr, out = _Ctx(), _Collect()
    run(_Src(len(items)), Segmenter(chunk=1.0, use_vad=False), _ASR(items), tr, out, tgt="zh", audio_clock=True,
        **kw)
    return tr.calls, out.rows


LINES = [("Мы создали новую ветку в репозитории.", "ru"), ("Удали старую ветку.", "ru")]


# ---- Acceptance (1): flag off -> translator calls unchanged ----
def test_off_by_default_no_context_argument():
    calls, rows = _run(LINES)
    assert [c[2] for c in calls] == [None, None]
    assert [r.target for r in rows] == [f"[{t}]" for t, _ in LINES]


def test_off_translate_checked_calls_engine_with_three_arguments():
    eng = _Plain()  # would raise TypeError on context=
    assert translate_checked(eng, "Hello there.", "en", "ru").text == "Hello there."
    assert eng.calls == ["Hello there."]


# ---- Acceptance (2): flag on -> the second line gets the first as context ----
def test_on_second_line_gets_first_as_context():
    calls, rows = _run(LINES, mt_context=True)
    assert calls == [(LINES[0][0], "ru", None), (LINES[1][0], "ru", LINES[0][0])]
    assert [r.original for r in rows] == [t for t, _ in LINES]  # the context is never part of the shown line
    assert [r.target for r in rows] == [f"[{t}]" for t, _ in LINES]


def test_on_with_sentence_merger():
    calls, _ = _run(LINES, mt_context=True, merger=SentenceMerger(min_words=1))
    assert [c[2] for c in calls] == [None, LINES[0][0]]


def test_on_language_change_drops_context():
    items = [LINES[0], ("Delete the old branch.", "en"), LINES[1]]
    calls, _ = _run(items, mt_context=True)
    assert [c[2] for c in calls] == [None, None, None]


def test_pause_of_four_seconds_drops_context():
    c = LineContext()
    c.after(None, "ru", "Мы создали новую ветку.", end=10.0)
    assert c.before(None, "ru", 10.0 + CONTEXT_MAX_PAUSE_S - 0.1) == "Мы создали новую ветку."
    assert c.before(None, "ru", 10.0 + CONTEXT_MAX_PAUSE_S) is None
    assert c.before(None, "ru", 20.0) is None


def test_language_change_clears_and_other_speaker_is_separate():
    c = LineContext()
    c.after("A", "ru", "Мы создали ветку.", end=1.0)
    assert c.before("B", "ru", 1.5) is None  # another speaker: their own stream
    assert c.before("A", "en", 1.5) is None
    c.after("A", "en", "Delete it.", end=2.0)
    assert c.before("A", "ru", 2.5) is None  # back to ru right after an en line: no stale ru context


def test_forced_line_is_not_context_and_clears_memory():
    c = LineContext()
    c.after(None, "ru", "Мы создали ветку.", end=1.0)
    c.after(None, "ru", "и потом", end=2.0, finished=False)
    assert c.before(None, "ru", 2.5) is None


def test_only_last_sentence_is_kept():
    assert last_sentence("Первая фраза. Вторая фраза!") == "Вторая фраза!"
    assert last_sentence("без точки") == "без точки"
    c = LineContext()
    c.after(None, "ru", "Первая фраза. Вторая фраза.", end=1.0)
    assert c.before(None, "ru", 1.5) == "Вторая фраза."


def test_replacement_line_uses_the_line_before_the_replaced_one():
    c = LineContext()
    c.after(None, "en", "We use git.", end=1.0)
    c.after(None, "en", "Delete the old.", end=3.0)
    # #1395: "Delete the old." + late "Branch." is re-sent in its place: its context is "We use git."
    assert c.before(None, "en", 1.5, replaces=True) == "We use git."
    c.after(None, "en", "Delete the old branch.", end=4.0, replaces=True)
    assert c.before(None, "en", 4.5) == "Delete the old branch."
    assert c.before(None, "en", 1.5, replaces=True) == "We use git."


# ---- engines / wrappers ----
def test_translate_checked_passes_context_only_to_engines_that_take_it():
    eng = _Ctx()
    translate_checked(eng, "Удали ветку.", "ru", "zh", context="Мы используем git.")
    assert eng.calls[-1][2] == "Мы используем git."
    plain = _Plain()
    assert translate_checked(plain, "Удали ветку.", "ru", "zh", context="Мы используем git.") is not None
    assert plain.calls == ["Удали ветку."]


def test_pending_translator_forwards_context_once_loaded():
    eng = _Ctx()
    p = PendingTranslator(lambda: False, lambda: eng)
    assert translate_checked(p, "x y", "ru", "zh", context="c") is not None  # not loaded yet: no error
    p._inner = eng
    translate_checked(p, "Удали ветку.", "ru", "zh", context="Мы используем git.")
    assert eng.calls[-1][2] == "Мы используем git."


def test_switchable_drops_context_for_a_lower_step_without_it():
    plain = _Plain()
    sw = SwitchableTranslator(Ladder("mt", "low", plain))
    assert sw.translate("Удали ветку.", "ru", "zh", context="Мы используем git.").text == "Удали ветку."


# ---- Acceptance (3) ----
def test_help_lists_mt_context(capsys):
    from tinglongwu import cli

    try:
        cli.parse_args(["--help"])
    except SystemExit:
        pass
    assert "--mt-context" in capsys.readouterr().out
    assert cli.parse_args(["--mt-context"]).mt_context is True
    assert cli.parse_args([]).mt_context is False
