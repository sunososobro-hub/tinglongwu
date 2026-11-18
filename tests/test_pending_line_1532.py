"""#1532: a heard line shows at once (dim source + mark) in its place; its translation fills that same line."""
import importlib.util
import queue
import sys
import threading
import time
from pathlib import Path

import numpy as np

from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.gui import core
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.sink import Result
from tinglongwu.translate import Translation, Translator

P = core.PENDING_MARK
ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("eval_livelag_1532", ROOT / "scripts" / "eval_livelag.py")
livelag = importlib.util.module_from_spec(_spec)
sys.modules["eval_livelag_1532"] = livelag
_spec.loader.exec_module(livelag)


def _feed(st, sink, q):
    while not q.empty():
        st.apply(q.get())


def test_pending_line_every_heard_line_shows_in_order_before_mt():
    """Two lines heard while MT is busy: both are on screen (dim), oldest first - not only the newest one."""
    q, st = queue.Queue(), core.SubtitleState(10)
    sink = core.QueueSink(q)
    sink.write(Result("en", "zero", None, "零"))
    sink.write_source("en", "one")
    sink.write_source("en", "two")
    _feed(st, sink, q)
    assert st.lines() == [("零", False), ("one" + P, True), ("two" + P, True)]
    assert st.history() == [("zero", "零", False), ("", "one" + P, True), ("", "two" + P, True)]


def test_pending_line_translation_fills_its_own_row_in_place():
    q, st = queue.Queue(), core.SubtitleState(10)
    sink = core.QueueSink(q)
    for s in ("one", "two", "three"):
        sink.write_source("en", s)
    _feed(st, sink, q)
    sink.write(Result("en", "one", None, "一"))
    _feed(st, sink, q)
    assert st.lines() == [("一", False), ("two" + P, True), ("three" + P, True)]  # nothing else moved
    # out of order: "three" comes back before "two" - each lands on its own line
    sink.write(Result("en", "three", None, "三"))
    _feed(st, sink, q)
    assert st.lines() == [("一", False), ("two" + P, True), ("三", False)]
    sink.write(Result("en", "two", None, "二"))
    _feed(st, sink, q)
    assert st.lines() == [("一", False), ("二", False), ("三", False)]
    assert st.pending == []


def test_pending_line_source_only_final_when_mt_not_installed():
    """#1357: MT not ready -> the final has no target; the line stays source text (no mark, not dim)."""
    q, st = queue.Queue(), core.SubtitleState(5)
    sink = core.QueueSink(q)
    sink.write_source("en", "hello there")
    sink.write(Result("en", "hello there", None, "", "zh"))
    _feed(st, sink, q)
    assert st.lines() == [("hello there", False)]


def test_pending_line_echo_dropped_line_leaves_no_stuck_row(monkeypatch):
    from tinglongwu.reverse import echoguard

    class Echo:
        def check(self, *a):
            return True

    monkeypatch.setattr(echoguard, "shared", lambda: Echo())
    q, st = queue.Queue(), core.SubtitleState(5)
    sink = core.QueueSink(q)
    sink.write_source("en", "my own voice")
    _feed(st, sink, q)
    assert st.lines() == [("my own voice" + P, True)]  # the row exists before the echo drops it
    sink.write(Result("en", "my own voice", None, "我自己的聲音"))
    _feed(st, sink, q)
    assert st.lines() == [] and st.pending == []


class Tone:
    def frames(self):
        yield np.full(16000, 0.1, np.float32)

    def close(self):
        pass


class OneASR(Recognizer):
    def transcribe(self, audio):
        return Transcript("this is one line", "en")


class GatedMT(Translator):
    def __init__(self):
        self.started, self.go = threading.Event(), threading.Event()

    def translate(self, text, src, tgt):
        self.started.set()
        assert self.go.wait(5.0)
        return Translation(None, "這是一行")


def test_pending_line_pipeline_shows_source_before_mt_and_logs_src_shown_ms():
    q, mt, recs = queue.Queue(), GatedMT(), []
    st = core.SubtitleState(5)
    th = threading.Thread(target=run, args=(Tone(), Segmenter(chunk=1.0, use_vad=False), OneASR(), mt,
                                            core.QueueSink(q), "zh"), kwargs={"on_record": recs.append})
    th.start()
    assert mt.started.wait(5.0)
    _feed(st, None, q)
    assert st.lines() == [("this is one line" + P, True)]  # on screen while MT still runs
    time.sleep(0.2)  # MT takes a while: the translation comes clearly after the source
    mt.go.set()
    th.join(5.0)
    _feed(st, None, q)
    assert st.lines() == [("這是一行", False)]
    assert len(recs) == 1 and recs[0]["src_shown_ms"] is not None and 0 <= recs[0]["src_shown_ms"] <= 100
    assert recs[0]["total"] - recs[0]["t_source_shown"] >= 0.15  # handoff timing: source handed to the sink before the translation


def test_livelag_reports_src_and_mt_shown():
    recs = [{"at": 10.0 + i, "total": 2.0 + i, "t_source_shown": 0.5 + i * 0.1} for i in range(3)]
    recs.append({"at": 20.0, "total": 9.0, "t_source_shown": 0.4, "mt_skipped": True})  # untranslated: no mt lag
    res = livelag.score(recs, clip_s=60.0)
    assert res["src_shown_s"] == 0.55 and res["mt_shown_s"] == 3.0
    txt = livelag.report(res)
    assert "source 0.55 s" in txt and "translation 3.0 s" in txt
    assert livelag.shown_lags([{"at": 1.0, "total": 2.0}]) == (None, 2.0)  # a sink that shows no source
    assert "source unavailable" in livelag.report(livelag.score([{"at": 1.0, "total": 2.0}], clip_s=60.0))


def test_pending_line_overlay_prefixed_echo_is_dropped(monkeypatch):
    from tinglongwu import overlay
    from tinglongwu.reverse import echoguard

    class Echo:
        def check(self, *a):
            return True

    class Inner:
        def write_source(self, *a, **k):
            pass

        def write(self, r):
            pass

    monkeypatch.setattr(echoguard, "shared", lambda: Echo())
    q, st = queue.Queue(), core.SubtitleState(5)
    sink = overlay.OverlaySink(Inner(), q, prefix="◀ ")
    sink.write_source("en", "my own voice")
    _feed(st, sink, q)
    assert st.lines() == [("my own voice" + P, True)]  # the row exists before the echo drops it
    sink.write(Result("en", "my own voice", None, "我自己的聲音"))
    _feed(st, sink, q)
    assert st.lines() == [] and st.pending == []
