"""#322 part 2: `tlwu-speak --overlay` — speak lines (▶) and, with --listen, the other side (◀) on one bar."""
import io
import queue
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from tinglongwu import overlay
from tinglongwu.reverse import proto
from tinglongwu.sink import Result, TextSink


class FakeMT:
    def translate(self, text, src, tgt):
        return SimpleNamespace(text=f"privet-{len(text)}" if tgt == "ru" else "zh-back")


class FakeTTS:
    def __init__(self):
        self.killed = threading.Event()
        self.proc = SimpleNamespace(kill=self.killed.set)

    def speak(self, text):
        yield {"t": "chunk", "sr": 24000}, np.ones(4, np.float32).tobytes()
        yield {"t": "done"}, None


class FakePlayer:
    enabled = True

    def push(self, audio, sr):
        pass


def _mock_live(monkeypatch, texts=("你好",)):
    monkeypatch.setattr("tinglongwu.reverse.live.capture_segments", lambda src, seg, stop, lat=None: iter([1]))
    monkeypatch.setattr("tinglongwu.reverse.live.recognize_utterances",
                        lambda segs, asr, gate=None, src="zh", **kw: iter(texts))
    monkeypatch.setattr("tinglongwu.capture.mic.MicSource", lambda name: object())


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_flag_off_by_default_and_needs_mic():
    ap = proto.build_parser()
    assert ap.parse_args([]).overlay is False and ap.parse_args(["--mic", "--overlay"]).overlay is True
    with pytest.raises(SystemExit) as e:
        proto.main(["--overlay"])  # batch --input has no live lines to show
    assert e.value.code == 2


@pytest.mark.parametrize("mode", ["auto", "text", "confirm"])
def test_live_lines_go_to_bar_marked_me(monkeypatch, mode):
    _mock_live(monkeypatch)
    if mode == "confirm":
        monkeypatch.setattr("builtins.input", lambda *x: "y")
    q = queue.Queue()
    a = SimpleNamespace(mic_name="Headset Mic", chunk_s=10.0, to="ru", bar_q=q, no_back=True)
    pv = FakePlayer()
    pv.enabled = False
    proto._live_main(a, None, FakeMT(), None if mode == "text" else FakeTTS(), FakePlayer(), pv,
                     mode == "confirm", {"items": []})
    assert _drain(q) == [("final", "你好", "▶ privet-2")]


def test_without_overlay_nothing_is_queued(monkeypatch):
    _mock_live(monkeypatch)
    a = SimpleNamespace(mic_name="Headset Mic", chunk_s=10.0, to="ru")
    res = {"items": []}
    proto._live_main(a, None, FakeMT(), FakeTTS(), FakePlayer(), SimpleNamespace(enabled=False), False, res)
    assert [r["decision"] for r in res["items"]] == ["sent"]


def test_bar_quit_ends_a_pending_confirm_prompt_and_kills_tts(monkeypatch):
    """The worker sits in the confirm prompt; the bar's Quit (ext_stop) must still stop TTS and capture."""
    _mock_live(monkeypatch)
    release = threading.Event()
    monkeypatch.setattr("builtins.input", lambda *x: (release.wait(5), "d")[1])
    stop, tts = threading.Event(), FakeTTS()
    a = SimpleNamespace(mic_name="Headset Mic", chunk_s=10.0, to="ru", bar_q=queue.Queue(), ext_stop=stop,
                        no_back=True)
    pl = FakePlayer()
    th = threading.Thread(target=proto._live_main,
                          args=(a, None, FakeMT(), tts, pl, SimpleNamespace(enabled=False), True, {"items": []}),
                          daemon=True)
    th.start()
    time.sleep(0.2)
    assert not tts.killed.is_set()
    stop.set()
    assert tts.killed.wait(2) and pl.enabled is False
    release.set()
    th.join(5)


def test_listen_side_lines_go_to_bar_marked_them(capsys):
    q, seen = queue.Queue(), {}

    def run(a, stop, sink=None):
        seen["sink"] = sink
        sink.write(Result("ru", "privet", None, "你好", tgt="zh"))

    side = proto.ListenSide(["--lang", "ru"], run=run, bar_q=q).start()
    side.thread.join(5)
    assert side.error is None and _drain(q) == [("final", "privet", "◀ 你好")]
    assert "你好" in capsys.readouterr().out  # the terminal still shows it


def test_listen_overlay_flag_is_refused_with_a_hint():
    with pytest.raises(ValueError, match="before --listen"):
        proto.ListenSide(["--lang", "ru", "--overlay"])


def test_main_overlay_runs_speak_on_the_bar_worker(monkeypatch):
    got = {}

    def fake_main(argv, vm, bar_q=None, stop=None):
        got.update(argv=argv, q=bar_q, stop=stop)
        return 0

    def fake_run(work):
        return work("Q", "STOP")

    monkeypatch.setattr(proto, "_main", fake_main)
    monkeypatch.setattr(overlay, "run_listen", fake_run)
    assert proto.main(["--mic", "--overlay"]) == 0
    assert got == {"argv": ["--mic", "--overlay"], "q": "Q", "stop": "STOP"}


class _Bar:
    def __init__(self, q, stop, path=None):
        self.stop = stop

    def run(self):
        self.stop.wait(5)


def test_run_listen_returns_work_exit_code_and_waits_for_cleanup(monkeypatch, capsys):
    monkeypatch.setattr(overlay, "Bar", _Bar)
    cleaned = []

    def work(q, stop):
        time.sleep(0.05)
        cleaned.append(1)
        return 3

    assert overlay.run_listen(work) == 3 and cleaned == [1]

    def bad_args(q, stop):
        raise SystemExit("no model")

    assert overlay.run_listen(bad_args) == 2 and "no model" in capsys.readouterr().err


def test_overlay_sink_prefix_keeps_terminal_output():
    out, q = io.StringIO(), queue.Queue()
    s = overlay.OverlaySink(TextSink(stream=out), q, prefix="◀ ")
    s.write(Result("en", "hello", None, "hello", tgt="en"))  # same language: the source itself
    assert _drain(q) == [("final", "hello", "◀ hello")] and "hello" in out.getvalue()
