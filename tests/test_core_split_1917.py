"""#1917 part 5: subtitle rows (queue sinks, SubtitleState) and speaker labels live in gui/core_subtitles.py.

gui/core.py re-exports every moved name, so `from tinglongwu.gui.core import SubtitleState` and `core.PENDING_MARK`
keep working, and the moved code behaves as before.
"""

from __future__ import annotations

import queue
import subprocess
import sys

from tinglongwu.gui import core, core_subtitles
from tinglongwu.sink import Result

MOVED = ("SPEAK_MARK", "QueueSink", "wrap_bubble", "_zh_display", "SpeakQueueSink", "QueueDraftRenderer",
         "strip_silent_hint", "speaker_text", "PENDING_MARK", "SubtitleState", "SPK_PALETTE", "SPK_AVATAR_FG",
         "SpeakerColors", "speaker_prefix", "speaker_initial")


def test_core_reexports_the_same_objects():
    for n in MOVED:
        assert getattr(core, n) is getattr(core_subtitles, n), n
        obj = getattr(core_subtitles, n)
        if callable(obj):
            assert obj.__module__ == "tinglongwu.gui.core_subtitles", n  # defined there, no stale copy in core


def test_subtitles_module_imports_on_its_own():
    # core imports core_subtitles; importing core_subtitles first must not need a half-built core (no import cycle)
    code = ("import sys, tinglongwu.gui.core_subtitles as s; "
            "assert 'tinglongwu.gui.core' not in sys.modules; print(s.SubtitleState(2).lines())")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


def _res(original, target, src="en", tgt="zh-tw"):
    return Result(src=src, original=original, pivot=None, target=target, tgt=tgt)


def test_queue_sink_into_subtitle_state():
    q: queue.Queue = queue.Queue()
    sink = core.QueueSink(q)
    st = core.SubtitleState(max_lines=3)
    sink.write_source("en", "hello")
    sink.write(_res("hello", "你好"))
    while not q.empty():
        st.apply(q.get())
    assert st.lines() == [("你好", False)]
    assert st.history() == [("hello", "你好", False)]


def test_pending_row_then_final_in_place():
    st = core.SubtitleState(max_lines=2)
    assert st.apply(("source", "a"))
    assert st.lines() == [("a" + core.PENDING_MARK, True)]
    assert st.apply(("final", "a", "A"))
    assert st.lines() == [("A", False)] and st.pending == []


def test_speak_sink_marks_my_lines():
    q: queue.Queue = queue.Queue()
    core.SpeakQueueSink(q).write(_res("hi", "やあ", src="en", tgt="ja"))
    assert q.get() == ("final_side", core.SPEAK_MARK + "hi", core.SPEAK_MARK + "やあ")


def test_wrap_bubble_and_speaker_colors():
    assert core.wrap_bubble("aa bb cc", len, 5) == ["aa bb", "cc"]
    colors = core.SpeakerColors()
    assert colors.color("x") == core.SPK_PALETTE[0] and colors.color("y") == core.SPK_PALETTE[1]
    assert colors.color("x") == core.SPK_PALETTE[0]
