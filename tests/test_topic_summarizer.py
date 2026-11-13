"""#609 (#595 part 2): topic notes are summarized in a low-priority background worker that never blocks the
ASR/MT line path, drain after the meeting, and land as Markdown next to the --save transcript."""
import os
import sys
import threading
import time

import pytest

from tinglongwu import topics
from tinglongwu.sink import JsonlSaveSink, Result

SUMMARY = ("TITLE: Budget for Q3\n- cut travel by 10%\n- hire one tester\n- move launch to May\n"
           "ACTION me: send the revised budget\nACTION them: confirm the launch date\n")


class SlowModel:
    def __init__(self, delay=2.0):
        self.delay, self.calls, self.threads = delay, 0, set()

    def __call__(self, prompt):
        self.threads.add(threading.get_ident())
        time.sleep(self.delay)
        self.calls += 1
        return SUMMARY


def _feed(notes, n_topics, per_topic=14, step=5.0, pause=12.0):
    """n_topics topics of per_topic lines `step` s apart (>= 60 s each), separated by a `pause` s silence.
    Returns the slowest on_line call in seconds."""
    t, worst = 0.0, 0.0
    for k in range(n_topics):
        for i in range(per_topic):
            rec = {"t": t, "side": "me" if i % 2 else "them", "src": f"src {k}.{i}", "dst": f"line {k}.{i}"}
            t0 = time.perf_counter()
            notes.on_line(rec)
            worst = max(worst, time.perf_counter() - t0)
            t += step
        t += pause
    return worst


def test_slow_model_never_delays_the_line_path_and_queue_drains(tmp_path):
    model = SlowModel(2.0)
    notes = topics.TopicNotes(tmp_path / "m.topics.md", model)
    worst = _feed(notes, 3)
    assert worst < 0.05, f"on_line took {worst * 1000:.0f} ms while the model was busy"
    assert model.calls < 3  # the summaries were still running when the meeting ended (backlog)
    notes.close()
    assert model.calls == 3  # 2 closed by pauses + the open one flushed at the end
    assert threading.get_ident() not in model.threads  # the model never ran on the caller's (MT) thread
    md = (tmp_path / "m.topics.md").read_text(encoding="utf-8")
    assert md.count("## ") == 3


def test_markdown_has_title_bullets_and_who_actions(tmp_path):
    notes = topics.TopicNotes(tmp_path / "x.topics.md", lambda p: SUMMARY + "- extra 4\n- extra 5\n- extra 6\n")
    _feed(notes, 1)
    notes.close()
    md = (tmp_path / "x.topics.md").read_text(encoding="utf-8")
    sec = md.split("## 1. ", 1)[1]
    assert sec.startswith("Budget for Q3 (00:00-01:05)")
    assert sum(1 for ln in sec.splitlines() if ln.startswith("- ") and not ln.startswith("- [ ]")) == 5  # capped
    assert "- [ ] (me) send the revised budget" in sec
    assert "- [ ] (them) confirm the launch date" in sec


def test_prompt_carries_both_sides_and_prefers_the_translation(tmp_path):
    seen = []
    notes = topics.TopicNotes(tmp_path / "p.topics.md", lambda p: seen.append(p) or SUMMARY)
    notes.on_line({"t": 0, "side": "them", "src": "privet", "dst": "你好"})
    notes.on_line({"t": 3, "side": "me", "src": "早安", "dst": ""})
    notes.on_line({"t": 4, "fix": {"relabel": 0}})  # speaker corrections are not transcript lines
    notes.close()
    assert len(seen) == 1
    assert "[them] 你好" in seen[0] and "[me] 早安" in seen[0] and "relabel" not in seen[0]


def test_interim_segmenter_pause_min_and_max():
    seg, L = topics.PauseSegmenter(), topics.Line
    assert seg.add(L(0, "them", "a")) == [] and seg.add(L(30, "them", "b")) == []
    assert seg.add(L(45, "me", "c")) == []  # 15 s pause but the topic is only 30 s long: stays open
    assert seg.add(L(65, "me", "d")) == []  # 65 s long now
    closed = seg.add(L(80, "them", "e"))  # 15 s pause on a >= 60 s topic: closes
    assert [ln.text for ln in closed[0]] == ["a", "b", "c", "d"]
    out = []
    for i in range(1, 100):  # one line every 5 s, never a pause: cut at MAX_S
        out += seg.add(L(80 + i * 5, "them", str(i)))
    assert len(out) == 1 and out[0][-1].t - out[0][0].t <= topics.MAX_S
    assert len(seg.flush()) == 1 and seg.flush() == []


def test_model_error_is_logged_and_the_next_topic_still_runs(tmp_path):
    logs, calls = [], []

    def flaky(p):
        calls.append(p)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return SUMMARY

    notes = topics.TopicNotes(tmp_path / "e.topics.md", flaky, log=logs.append)
    _feed(notes, 2)
    notes.close()
    assert len(calls) == 2 and any("boom" in m for m in logs)
    assert (tmp_path / "e.topics.md").read_text(encoding="utf-8").count("## ") == 1


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="per-thread nice is Linux; Windows uses SetThreadPriority")
def test_worker_runs_at_lowest_priority():
    got = {}

    def model(p):
        got["nice"] = os.getpriority(os.PRIO_PROCESS, threading.get_native_id())
        return SUMMARY

    w = topics.TopicSummarizer(model, lambda t: None)
    w.submit([topics.Line(0, "me", "x")])
    w.close(10)
    assert w.lowered is True and got["nice"] == 19
    assert os.getpriority(os.PRIO_PROCESS, threading.get_native_id()) != 19  # caller thread untouched


def test_save_sink_feeds_notes_from_both_sides(tmp_path):
    saver = JsonlSaveSink(None, tmp_path / "t.jsonl", clock=iter([0, 1, 2]).__next__)
    seen = []
    notes = topics.attach(saver, "unused.gguf", summarize=lambda p: seen.append(p) or SUMMARY)
    assert notes.path == tmp_path / "t.topics.md"
    saver.record(Result("ru", "privet", None, "你好", tgt="zh"))
    saver.record(Result("zh", "早安", None, "dobroe utro", tgt="ru", side="me"))
    notes.close()
    assert "[them] 你好" in seen[0] and "[me] dobroe utro" in seen[0]
    assert (tmp_path / "t.topics.md").exists()


def test_broken_notes_never_break_the_transcript(tmp_path):
    saver = JsonlSaveSink(None, tmp_path / "t.jsonl")
    saver.on_line = lambda rec: 1 / 0
    saver.record(Result("ru", "privet", None, "你好", tgt="zh"))
    assert "privet" in (tmp_path / "t.jsonl").read_text(encoding="utf-8")


def test_cli_topics_is_off_by_default_and_needs_save(tmp_path, capsys):
    from tinglongwu import cli

    assert cli.parse_args(["--file", "x.wav"]).topics is None
    m = tmp_path / "s.gguf"
    m.write_bytes(b"GGUF")
    with pytest.raises(SystemExit):
        cli.parse_args(["--file", "x.wav", "--topics", str(m)])
    assert "--topics needs --save" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.parse_args(["--file", "x.wav", "--save", "t.jsonl", "--topics", str(tmp_path / "missing.gguf")])
    assert cli.parse_args(["--file", "x.wav", "--save", "t.jsonl", "--topics", str(m)]).topics == str(m)
