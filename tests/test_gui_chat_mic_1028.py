"""#1028 mic toggle: muting feeds the speak leg's capture silence (MuteSource, ported from #1038) while the listen leg keeps running."""
import threading

import numpy as np

from tinglongwu import cli
from tinglongwu.capture.base import MuteSource

SR = 16000


class Src:
    def __init__(self, n=4):
        self.n, self.closed, self.gen_closed = n, False, False

    def frames(self):
        try:
            for i in range(self.n):
                yield np.full(SR // 10, 0.5 + i, dtype=np.float32)
        finally:
            self.gen_closed = True

    def close(self):
        self.closed = True


def test_mute_source_silences_frames_only_while_muted():
    muted = threading.Event()
    inner = Src()
    src = MuteSource(inner, muted)
    out = []
    for i, f in enumerate(src.frames()):
        out.append(f)
        if i == 0:
            muted.set()  # mic off after the first frame
        elif i == 2:
            muted.clear()  # mic back on
    assert [len(f) for f in out] == [SR // 10] * 4  # same length: the stream clock does not jump
    assert [float(f.max()) for f in out] == [0.5, 0.0, 0.0, 3.5]
    assert inner.gen_closed
    src.close()
    assert inner.closed


def test_mute_source_closes_the_capture_generator_on_early_exit():
    inner = Src(10)
    gen = MuteSource(inner, threading.Event()).frames()
    next(gen)
    gen.close()
    assert inner.gen_closed


def _session_source(tmp_path, monkeypatch, mute):
    """Run cli.run_session with fake capture/ASR and return the audio source the pipeline got."""
    import tinglongwu.asr
    import tinglongwu.pipeline

    got = {}
    monkeypatch.setattr(tinglongwu.pipeline, "run", lambda source, *a, **k: got.setdefault("source", source))
    monkeypatch.setattr(tinglongwu.asr, "FasterWhisperRecognizer", lambda *a, **k: type("A", (), {"device": "cpu"})())
    monkeypatch.setattr(cli, "select_backend", lambda *a, **k: Src())
    monkeypatch.setattr(cli, "models_dir", lambda d=None: tmp_path)
    monkeypatch.setattr("tinglongwu.minspec.check_avx2", lambda *a, **k: None)
    monkeypatch.setenv("TLWU_UI_LANG", "en")
    monkeypatch.chdir(tmp_path)
    a = cli.parse_args(["--transcript-only", "--no-adapt", "--model", "tiny", "--device", "cpu", "--mic"])
    a.mic_mute = mute
    cli.run_session(a)
    return got["source"]


def test_run_session_wraps_only_a_leg_that_has_a_mute_event(tmp_path, monkeypatch):
    plain = _session_source(tmp_path, monkeypatch, None)
    assert isinstance(plain, Src)  # the listen leg (no mute event) gets its capture unchanged
    muted = threading.Event()
    src = _session_source(tmp_path, monkeypatch, muted)
    assert isinstance(src, MuteSource)
    muted.set()
    assert all(float(abs(f).max()) == 0.0 for f in src.frames())


def test_mic_toggle_and_bubbles(tmp_path, monkeypatch):
    tk = __import__("pytest").importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        __import__("pytest").skip("no display")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from tinglongwu.gui import app

    a = app.App()
    try:
        assert a.mic_off is False and not a.mic_ev.is_set()
        a.v_mic.set(False)
        a._mic_toggled()
        assert a.mic_off is True and a.mic_ev.is_set() and a.btn_mic.cget("text") == "Mic off"
        a.v_notice.set("no CABLE")  # un-mute must not wipe an unrelated notice
        a.listening = True
        a.v_mic.set(True)
        a._mic_toggled()
        assert a.v_notice.get() == "no CABLE" and not a.mic_ev.is_set()
        a.listening = False
        a.state.apply(("final", "hi", "你好"))
        a.state.apply(("final_side", "🎤 ok", "🎤 好"))
        a.render_log()
        txt = a.log_text.get("1.0", "end-1c")
        assert "Other side" in txt and "Me" in txt and "🎤" not in txt
        assert a.log_text.tag_ranges("other_final") and a.log_text.tag_ranges("me_final")
    finally:
        a.root.destroy()


def test_bubbles_hug_text_and_show_the_original_small_1052(tmp_path, monkeypatch):
    tk = __import__("pytest").importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        __import__("pytest").skip("no display")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from tinglongwu.gui import app

    a = app.App()
    try:
        assert a.settings["show_src"] is False  # default: the original still shows in the chat
        a.state.apply(("final", "hello", "你好"))
        a.state.apply(("final_side", "🎤 我說中文", "🎤 I speak Chinese"))
        a.render_log()
        me_src = a.log_text.tag_ranges("me_src")
        assert me_src and a.log_text.get(me_src[0], me_src[1]).strip() == "我說中文"
        other_src = a.log_text.tag_ranges("other_src")
        assert other_src and a.log_text.get(other_src[0], other_src[1]).strip() == "hello"
        # the bubble background must not cover the line break (that made it full width)
        for r in range(0, len(a.log_text.tag_ranges("me_final")), 2):
            assert not a.log_text.get(a.log_text.tag_ranges("me_final")[r], a.log_text.tag_ranges("me_final")[r + 1]).endswith("\n")
        assert a.log_text.tag_cget("me_base", "justify") == "right"
    finally:
        a.root.destroy()
