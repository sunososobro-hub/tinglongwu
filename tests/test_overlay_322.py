"""#322: --overlay subtitle bar for CLI listen mode (no display needed except the last test)."""
import io
import json
import os
import queue
import threading
import time

import pytest

from tinglongwu import cli, overlay
from tinglongwu.gui.core import SubtitleState
from tinglongwu.sink import Result, TextSink


def test_overlay_off_by_default_and_flag_parses():
    assert cli.parse_args([]).overlay is False and cli.parse_args(["--overlay"]).overlay is True


def test_sink_prints_and_feeds_bar_with_max_lines():
    out, q = io.StringIO(), queue.Queue()
    sink = overlay.OverlaySink(TextSink(stream=out), q)
    for i in range(5):
        sink.write_source("ru", f"privet {i}")
        sink.write(Result("ru", f"privet {i}", None, f"你好 {i}", tgt="zh"))
    assert "你好 4" in out.getvalue()  # the terminal still gets every line
    st = SubtitleState(max_lines=3)
    while not q.empty():
        st.apply(q.get_nowait())
    assert [t for t, _ in st.lines()] == ["你好 2", "你好 3", "你好 4"] and st.source() == "privet 4"


def test_position_and_font_persist_across_restart_and_keep_other_settings(tmp_path):
    p = tmp_path / "gui.json"
    p.write_text(json.dumps({"theme": "light", "model": "medium", "x": 5}), "utf-8")
    s = overlay.load(p)
    s.update(x=120, y=640, font_size=40, overlay_lines=4)
    assert overlay.save(s, p)
    data = json.loads(p.read_text("utf-8"))
    assert data["theme"] == "light" and data["model"] == "medium"  # GUI settings untouched
    again = overlay.load(p)  # "restart"
    assert (again["x"], again["y"], again["font_size"], again["overlay_lines"]) == (120, 640, 40, 4)


class _Root:
    def __init__(self):
        self.after_calls, self.quit_called = [], False

    def after(self, ms, fn):
        self.after_calls.append(ms)

    def quit(self):
        self.quit_called = True


def test_poll_renders_new_lines_saves_moves_and_stops(tmp_path):
    b = object.__new__(overlay.Bar)
    b.q, b.stop, b.path = queue.Queue(), threading.Event(), tmp_path / "gui.json"
    b.settings = overlay.load(b.path)
    b.saved = {k: b.settings.get(k) for k in overlay.KEYS}
    b.state, b.root, rendered = SubtitleState(3), _Root(), []
    b.overlay = type("O", (), {"render": lambda self, st: rendered.append([t for t, _ in st.lines()])})()
    b.q.put(("final", "hello", "你好"))
    b.poll()
    assert rendered == [["你好"]] and b.root.after_calls == [overlay.POLL_MS] and overlay.POLL_MS < 100
    assert not b.path.exists()  # nothing moved: nothing written
    b.settings["x"], b.settings["font_size"] = 300, 44  # the user dragged the bar / pressed A+
    b.poll()
    assert json.loads(b.path.read_text("utf-8"))["x"] == 300 and len(rendered) == 1
    b.stop.set()
    b.poll()
    assert b.root.quit_called and len(b.root.after_calls) == 2


def test_listen_overlay_runs_pipeline_on_worker_and_returns_its_error(monkeypatch, capsys):
    got = []

    class B:
        def __init__(self, q, stop, path=None):
            self.q, self.stop = q, stop

        def run(self):
            assert self.stop.wait(5)
            got.append(self.q.get_nowait())

    monkeypatch.setattr(overlay, "Bar", B)

    def work(q, stop):
        q.put(("final", "a", "b"))
        raise RuntimeError("model not found")

    assert overlay.run_listen(work) == 2
    assert got == [("final", "a", "b")] and "model not found" in capsys.readouterr().err


def test_real_bar_shows_a_line_within_100ms(tmp_path):
    tk = pytest.importorskip("tkinter")
    if os.name != "nt" and not os.environ.get("DISPLAY"):
        pytest.skip("no display")
    try:
        bar = overlay.Bar(queue.Queue(), threading.Event(), tmp_path / "gui.json")
    except tk.TclError as e:
        pytest.skip(f"no display: {e}")
    try:
        t0 = time.monotonic()
        bar.q.put(("final", "hello", "你好"))
        while "你好" not in bar.overlay.text.get("1.0", "end") and time.monotonic() - t0 < 1:
            bar.root.update()
        assert time.monotonic() - t0 < 0.1 and bar.overlay.win.attributes("-topmost")
    finally:
        bar.root.destroy()
