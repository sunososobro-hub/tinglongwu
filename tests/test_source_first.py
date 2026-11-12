"""#53: the source line is printed as soon as ASR is done; the [ZH] lines follow when MT finishes."""
import datetime
import io
import queue
import threading

import numpy as np

from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.gui import core
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.sink import JsonlSaveSink, Result, TextSink
from tinglongwu.translate import Translation, Translator

NOW = lambda: datetime.datetime(2026, 1, 1, 12, 0, 0)  # noqa: E731


class Tone:
    def __init__(self, n):
        self.n = n

    def frames(self):
        for _ in range(self.n):
            yield np.full(16000, 0.1, np.float32)

    def close(self):
        pass


class LineASR(Recognizer):
    def __init__(self):
        self.n = 0

    def transcribe(self, audio):
        self.n += 1
        return Transcript(f"line {self.n}", "ru")


class GatedMT(Translator):
    """Holds the first translation until the test has looked at stdout."""

    def __init__(self):
        self.started, self.go = threading.Event(), threading.Event()

    def translate(self, text, src, tgt):
        self.started.set()
        assert self.go.wait(5.0)
        return Translation(None, "zh " + text)


def test_source_line_shows_before_mt_finishes():
    out, mt = io.StringIO(), GatedMT()
    t = threading.Thread(target=run, args=(Tone(1), Segmenter(chunk=1.0, use_vad=False), LineASR(), mt,
                                           TextSink(out, now=NOW), "zh"))
    t.start()
    assert mt.started.wait(5.0)
    assert out.getvalue() == "12:00:00 ◀ Them line 1\n"  # visible while MT still runs
    mt.go.set()
    t.join(5.0)
    assert out.getvalue() == "12:00:00 ◀ Them line 1\n" + " " * 16 + "→ [ZH] zh line 1\n\n"


def test_backlog_keeps_whole_blocks_without_repeats():
    out = io.StringIO()
    s = TextSink(out, now=NOW)
    s.write_source("ru", "a")
    s.write_source("ru", "b")  # MT busy with "a": not printed yet
    assert out.getvalue() == "12:00:00 ◀ Them a\n"
    s.write(Result("ru", "a", None, "A"))
    assert out.getvalue().endswith("→ [ZH] A\n\n12:00:00 ◀ Them b\n")  # b shown as soon as a's block is done
    s.write(Result("ru", "b", None, "B"))
    want = "".join(f"12:00:00 ◀ Them {x}\n" + " " * 16 + f"→ [ZH] {x.upper()}\n\n" for x in "ab")
    assert out.getvalue() == want


def test_other_line_between_ends_dangling_source():
    out = io.StringIO()
    s = TextSink(out, now=NOW)
    s.write_source("ru", "a")
    s.write(Result("zh", "你好", None, "你好"))  # a line that never announced its source
    s.write(Result("ru", "a", None, "A"))
    assert out.getvalue().count("Them a\n") == 2 and "Them a\n\n12:00:00 ◀ Them 你好\n" in out.getvalue()


def test_save_file_gets_finished_lines_only(tmp_path):
    out = io.StringIO()
    s = JsonlSaveSink(TextSink(out, now=NOW), tmp_path / "t.jsonl")
    s.write_source("ru", "a")
    assert out.getvalue() == "12:00:00 ◀ Them a\n" and (tmp_path / "t.jsonl").read_text() == ""
    s.write(Result("ru", "a", None, "A"))
    assert '"src": "a"' in (tmp_path / "t.jsonl").read_text()


def test_gui_shows_heard_source_dim_until_final():
    q = queue.Queue()
    sink, st = core.QueueSink(q), core.SubtitleState(2)
    sink.write_source("ru", "a")
    sink.write_source("ru", "b")
    sink.write(Result("ru", "a", None, "A"))
    evs = []
    while not q.empty():
        evs.append(q.get())
        st.apply(evs[-1])
        if evs[-1][0] == "source" and evs[-1][1] == "a":
            assert st.lines() == [("a", True)]
    assert st.lines() == [("A", False), ("b", True)]  # b still waits for MT
    st.apply(("final", "b", "B"))
    assert st.lines() == [("A", False), ("B", False)]
