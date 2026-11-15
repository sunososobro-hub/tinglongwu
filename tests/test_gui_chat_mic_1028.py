"""#1028: chat-style main window, mic toggle (speak-leg capture muted, listen leg untouched)."""
from tinglongwu.stream import MuteSource


class _Src:
    def __init__(self):
        self.closed = False

    def frames(self):
        yield from (1, 2, 3, 4)

    def close(self):
        self.closed = True


def test_mute_source_drops_frames_only_while_muted():
    asks = iter([False, True, False, False])  # frame 2 arrives while muted
    src = MuteSource(_Src(), lambda: next(asks))
    assert list(src.frames()) == [1, 3, 4]
    src.close()
    assert src.inner.closed


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
        assert a.mic_off is False
        a.v_mic.set(False)
        a._mic_toggled()
        assert a.mic_off is True and a.btn_mic.cget("text") == "Microphone off"
        a.state.apply(("final", "hi", "你好"))
        a.state.apply(("final_side", "🎤 ok", "🎤 好"))
        a.render_log()
        txt = a.log_text.get("1.0", "end-1c")
        assert "Other side" in txt and "Me" in txt and "🎤" not in txt
        assert a.log_text.tag_ranges("other_final") and a.log_text.tag_ranges("me_final")
    finally:
        a.root.destroy()
