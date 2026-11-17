"""#1395: a short late fragment is joined to the previous line, which is re-translated and replaces the shown line.

Example sentences are made up (generic software talk)."""
import json
import queue

import numpy as np

from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.gui.core import QueueSink, SubtitleState
from tinglongwu.merge import AMEND_WINDOW, SentenceMerger
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.sink import JsonlSaveSink, Result, Sink
from pivot_fake import PivotTranslator


def _m(**kw):
    return SentenceMerger(min_words=3, **kw)


def _texts(units):
    return [(u.text, u.replaces) for u in units]


# ---- merger ----
def test_window_constant():
    assert AMEND_WINDOW == 3.0


def test_ru_late_tail_joins_previous_line():
    m = _m()
    prev = "Сейчас покажу настройки для новой."
    assert _texts(m.add(prev, "ru", 0.0)) == [(prev, None)]
    out = m.add("Версии.", "ru", 1.2)
    assert _texts(out) == [("Сейчас покажу настройки для новой версии.", prev)]
    assert out[0].part == "Версии."
    assert m.buf == "" and m.due(10.0) == []  # nothing left to show as its own line


def test_en_late_tail_joins_previous_line():
    m = _m()
    prev = "We moved the logs to the new."
    m.add(prev, "en", 0.0)
    assert _texts(m.add("Folder.", "en", 2.5)) == [("We moved the logs to the new folder.", prev)]


def test_unfinished_tail_after_timeout_line_joins_on_release():
    m = _m(max_wait=1.0)
    assert _texts(m.add("мы сохраняем файл в папку", "ru", 0.0)) == []
    assert _texts(m.due(1.0)) == [("мы сохраняем файл в папку", None)]
    assert m.add("проекта", "ru", 1.5) == []  # unfinished: waits for more text as before
    assert _texts(m.due(2.5)) == [("мы сохраняем файл в папку проекта", "мы сохраняем файл в папку")]


def test_tail_after_window_stays_separate():
    m = _m()
    m.add("Сейчас покажу настройки для новой.", "ru", 0.0)
    assert m.add("Версии.", "ru", 3.5) == []  # held as before (#389), not joined
    assert _texts(m.due(5.0)) == [("Версии.", None)]


def test_questions_stay_separate_ru_en():
    for prev, q, lang in (("Мы закрыли эту задачу вчера.", "Почему?", "ru"),
                          ("We closed that ticket yesterday.", "Why?", "en")):
        m = _m()
        m.add(prev, lang, 0.0)
        assert m.add(q, lang, 1.0) == []
        assert _texts(m.flush()) == [(q, None)]


def test_capitalised_new_clause_stays_separate_ru_en():
    for prev, nxt, lang in (("Сохрани файл на диск.", "Потом перезапусти.", "ru"),
                            ("Save the file to disk.", "Then restart.", "en")):
        m = _m()
        m.add(prev, lang, 0.0)
        assert m.add(nxt, lang, 1.0) == []
        assert _texts(m.flush()) == [(nxt, None)]


def test_standalone_one_word_reply_stays_separate():
    for prev, nxt, lang in (("Ты обновил сборку на сервере.", "Да.", "ru"),
                            ("You updated the build on the server.", "Yes.", "en")):
        m = _m()
        m.add(prev, lang, 0.0)
        assert m.add(nxt, lang, 1.0) == []
        assert _texts(m.flush()) == [(nxt, None)]


def test_other_language_and_off_switches():
    m = _m()
    m.add("We moved the logs to the new.", "en", 0.0)
    assert m.add("Версии.", "ru", 1.0) == []
    m = _m(amend_window=0.0)
    m.add("We moved the logs to the new.", "en", 0.0)
    assert m.add("Folder.", "en", 1.0) == []
    m = SentenceMerger()  # min_words 0: fragments are not a thing, nothing is joined
    m.add("We moved the logs to the new.", "en", 0.0)
    assert _texts(m.add("Folder.", "en", 1.0)) == [("Folder.", None)]


# ---- sinks ----
def test_jsonl_save_has_the_merged_line_once(tmp_path):
    class Null(Sink):
        def write(self, r):
            pass

    p = tmp_path / "t.jsonl"
    s = JsonlSaveSink(Null(), p, clock=iter([0, 1, 2, 3]).__next__)
    s.write(Result("en", "First line here.", None, "A", "zh"))
    s.write(Result("en", "We moved the logs to the new.", None, "B", "zh"))
    s.write(Result("en", "We moved the logs to the new folder.", None, "B2", "zh",
                   replaces="We moved the logs to the new."))
    s.f.close()
    rows = [json.loads(x) for x in p.read_text("utf-8").splitlines()]
    assert [r["src"] for r in rows] == ["First line here.", "We moved the logs to the new folder."]
    assert rows[1]["dst"] == "B2"


def test_gui_state_replaces_the_shown_line():
    q = queue.Queue()
    sink, st = QueueSink(q), SubtitleState(3)
    sink.write(Result("en", "We moved the logs to the new.", None, "B", "zh"))
    sink.write(Result("en", "We moved the logs to the new folder.", None, "B2", "zh",
                      replaces="We moved the logs to the new."))
    while not q.empty():
        st.apply(q.get())
    assert st.lines() == [("B2", False)]


# ---- pipeline: --save output after a live-like run ----
class _ASR(Recognizer):
    def __init__(self, items):
        self.items = list(items)

    def transcribe(self, audio):
        return Transcript(self.items.pop(0), "ru")


class _Src:
    def __init__(self, n):
        self.n = n

    def frames(self):
        for _ in range(self.n):
            yield np.ones(16000, np.float32) * 0.3

    def close(self):
        pass


def test_pipeline_save_has_merged_line_once(tmp_path):
    class Collect(Sink):
        def __init__(self):
            self.rows = []

        def write(self, r):
            self.rows.append(r)

    tr = PivotTranslator(tmp_path, "tw", lambda s, t: (lambda x: x.upper()) if (s, t) == ("ru", "en")
                         else (lambda x: x))
    inner, p = Collect(), tmp_path / "s.jsonl"
    sink = JsonlSaveSink(inner, p)
    run(_Src(2), Segmenter(chunk=1.0, use_vad=False),
        _ASR(["Сейчас покажу настройки для новой.", "Версии."]), tr, sink,
        merger=SentenceMerger(min_words=3), audio_clock=True)
    sink.f.close()
    rows = [json.loads(x) for x in p.read_text("utf-8").splitlines()]
    assert [r["src"] for r in rows] == ["Сейчас покажу настройки для новой версии."]
    assert inner.rows[-1].replaces == "Сейчас покажу настройки для новой."
    assert "ВЕРСИИ" in rows[0]["dst"]  # the whole line was translated again


def test_buffered_tail_uses_its_arrival_time():
    m = SentenceMerger(min_words=3, max_wait=6.0)
    m.add("We moved the logs to the new", "en", 0.0)
    assert _texts(m.due(6.0)) == [("We moved the logs to the new", None)]
    assert m.add("project", "en", 8.5) == []  # arrives 2.5 s after the line
    out = m.add("folder.", "en", 9.5)  # finishes the tail 3.5 s after the line: still joined (arrival counts)
    assert _texts(out) == [("We moved the logs to the new project folder.", "We moved the logs to the new")]


def test_same_language_zh_replaces_the_converted_line(tmp_path, monkeypatch):
    from tinglongwu import translate

    monkeypatch.setattr(translate, "zh_convert", lambda text, variant: text.replace("设", "設"))

    class _ZhASR(_ASR):
        def transcribe(self, audio):
            return Transcript(self.items.pop(0), "zh")

    class Collect(Sink):
        def __init__(self):
            self.rows = []

        def write(self, r):
            self.rows.append(r)

    tr = PivotTranslator(tmp_path, "tw", lambda s, t: (lambda x: x))
    out = Collect()
    run(_Src(2), Segmenter(chunk=1.0, use_vad=False), _ZhASR(["我们先打开新版本的设置页面。", "下面。"]), tr, out,
        merger=SentenceMerger(min_words=3), audio_clock=True)
    assert out.rows[-1].replaces == out.rows[0].original == "我们先打开新版本的設置页面。"


def test_quoted_capitalised_clause_stays_separate():
    m = _m()
    m.add("We finished the migration.", "en", 0.0)
    assert m.add('"It failed."', "en", 1.0) == []
    assert _texts(m.flush()) == [('"It failed."', None)]


def test_unfinished_tail_at_end_of_input_still_joins():
    m = _m()
    m.add("We moved the logs to the new.", "en", 0.0)
    assert m.add("folder", "en", 1.0) == []
    assert _texts(m.flush()) == [("We moved the logs to the new folder", "We moved the logs to the new.")]


def test_unlabelled_tail_keeps_the_speaker_for_the_next_check(tmp_path):
    from tinglongwu.merge import Unit

    class Script(SentenceMerger):
        """Releases scripted units (speaker = tag[1]) one per add()."""

        def __init__(self, units):
            super().__init__()
            self.units = list(units)

        def add(self, *a, **k):
            return [self.units.pop(0)] if self.units else []

        def due(self, now):
            return []

        def flush(self):
            return []

    a = "We moved the logs to the new."
    b = "We moved the logs to the new project folder."
    units = [Unit(a, "en", False, (None, "A", 0)),
             Unit(b, "en", False, (None, None, 1), replaces=a, part="project folder."),
             Unit(b[:-1] + " this morning.", "en", False, (None, "B", 2), replaces=b, part="this morning.")]

    class Collect(Sink):
        def __init__(self):
            self.rows = []

        def write(self, r):
            self.rows.append(r)

    out = Collect()
    tr = PivotTranslator(tmp_path, "tw", lambda s, t: (lambda x: x))
    run(_Src(3), Segmenter(chunk=1.0, use_vad=False), _ASR(["x", "y", "z"]), tr, out,
        tgt="en", merger=Script(units), audio_clock=True)
    assert [(r.original, r.replaces, r.speaker) for r in out.rows] == [
        (a, None, "A"), (b, a, "A"), ("this morning.", None, "B")]


def test_record_amended_line_row_matches_its_clip(tmp_path):
    """#1424: --record must not pair the fragment-only clip with the merged text."""
    import wave

    from tinglongwu.record import Recorder

    tr = PivotTranslator(tmp_path, "tw", lambda s, t: (lambda x: x))
    class Null(Sink):
        def write(self, r):
            pass

    rec = Recorder(tmp_path / "rec")
    lens = []
    add = rec.add
    rec.add = lambda no, audio, start, end: (lens.append(len(audio)), add(no, audio, start, end))[1]
    run(_Src(2), Segmenter(chunk=1.0, use_vad=False),
        _ASR(["Сейчас покажу настройки для новой.", "Версии."]), tr, Null(),
        merger=SentenceMerger(min_words=3), audio_clock=True, recorder=rec)
    rec.close()
    rows = [json.loads(x) for x in (tmp_path / "rec" / "segments.jsonl").read_text("utf-8").splitlines()]
    assert [r["source"] for r in rows] == ["Сейчас покажу настройки для новой версии."]
    with wave.open(str(tmp_path / "rec" / rows[0]["file"])) as w:
        assert len(lens) == 2 and w.getnframes() == sum(lens)  # the audio of both segments, not just the fragment's
    assert sorted(p.name for p in (tmp_path / "rec").glob("*.wav")) == [rows[0]["file"]]


def _rec_result(src, replaces=None):
    return Result("ru", src, "t", "", "en", replaces=replaces)


def test_record_amend_does_not_repeat_the_shared_segment(tmp_path):
    """A segment split over the previous line and the fragment is heard once in the joined clip."""
    import wave

    from tinglongwu.record import Recorder

    rec = Recorder(tmp_path)
    rec.add(1, np.full(1600, 0.1, np.float32), 0.0, 0.1)
    rec.add(2, np.full(800, 0.2, np.float32), 0.1, 0.15)
    rec.line(rec.take(1, 1), _rec_result("a."))
    clip = rec.take(1, 2, amend=True)  # the fragment's text starts in segment 1, which line "a." already took
    assert len(clip[0]) == 800
    rec.line(clip, _rec_result("a. b.", replaces="a."))
    rec.close()
    rows = [json.loads(x) for x in (tmp_path / "segments.jsonl").read_text("utf-8").splitlines()]
    assert [r["source"] for r in rows] == ["a. b."]
    with wave.open(str(tmp_path / rows[0]["file"])) as w:
        assert w.getnframes() == 2400


def test_record_failed_amend_keeps_the_previous_row_and_clip(tmp_path, monkeypatch):
    import wave

    from tinglongwu import record
    from tinglongwu.record import Recorder

    rec = Recorder(tmp_path)
    rec.add(1, np.full(1600, 0.1, np.float32), 0.0, 0.1)
    rec.add(2, np.full(800, 0.2, np.float32), 0.1, 0.15)
    rec.line(rec.take(1, 1), _rec_result("a."))
    before = (tmp_path / "segments.jsonl").read_text("utf-8")

    class Boom:
        def __init__(self, *a, **k):
            raise OSError("disk full")

    monkeypatch.setattr(record.wave, "open", Boom)
    try:
        rec.line(rec.take(1, 2, amend=True), _rec_result("a. b.", replaces="a."))
    except OSError:
        pass
    monkeypatch.undo()
    rec.close()
    assert (tmp_path / "segments.jsonl").read_text("utf-8") == before
    with wave.open(str(tmp_path / "0001.wav")) as w:
        assert w.getnframes() == 1600
    assert not list(tmp_path.glob("*.tmp"))


def test_record_amend_without_new_audio_updates_the_row_and_keeps_the_clip(tmp_path):
    """The fragment's speech is already in the previous line's last segment: row text is amended, clip unchanged."""
    import wave

    from tinglongwu.record import Recorder

    rec = Recorder(tmp_path)
    rec.add(1, np.full(1600, 0.1, np.float32), 0.0, 0.1)
    rec.line(rec.take(1, 1), _rec_result("a."))
    clip = rec.take(1, 1, amend=True)
    assert clip is None
    rec.line(clip, _rec_result("a. b.", replaces="a."))
    rec.close()
    rows = [json.loads(x) for x in (tmp_path / "segments.jsonl").read_text("utf-8").splitlines()]
    assert [r["source"] for r in rows] == ["a. b."]
    with wave.open(str(tmp_path / rows[0]["file"])) as w:
        assert w.getnframes() == 1600
