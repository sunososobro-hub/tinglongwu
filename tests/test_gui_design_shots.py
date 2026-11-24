"""#1646: design-review screenshots of the main window in its real states (Tk under xvfb).

Set TLWU_GUI_SHOTS=<dir> to save design_<state>_<theme>_<width>.png (24 files); without it only the layout asserts run.
No model, audio device or network: the lines, topics and error come from fixtures below."""
import json
import os

import pytest

HEIGHT = 768
WIDTHS = (900, 1366, 1920)
THEMES = ("dark", "light")
STATES = ("idle", "meeting", "meeting_notes", "error")

LONG_LINE = ("This is a deliberately long sentence that keeps going so the wrapped bubble can be judged at every "
             "window width, including the narrow one, without any word being cut off at the edge " + "x" * 20)[:200]
LINES = [
    ("Good morning everyone, shall we start?", "大家早安,我們可以開始了嗎?", "me"),
    ("好的,先看第一季的預算。", "OK, let's look at the first-quarter budget first.", "other"),
    (LONG_LINE, "這是一句刻意拉長的句子," * 6, "other"),
    ("我們需要把差旅費用降低百分之十,並且在十二月一日之前確認發佈日期。", "We need to cut travel by ten percent and confirm the launch date by 1 December.", "other"),
    ("Sounds good, I will send the revised budget today.", "聽起來不錯,我今天會把修訂後的預算寄出。", "me"),
]


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    from tinglongwu import i18n
    from tinglongwu.gui import app, core

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(i18n, "_system_tag", lambda: "en-US")
    monkeypatch.setattr(i18n.userdirs, "write_config_value", lambda k, v: None)
    monkeypatch.setattr(i18n, "_override", "en")
    monkeypatch.setattr(core, "_gpu_usable", lambda: False)
    (tmp_path / "tinglongwu").mkdir()
    made = []

    def make(**saved):
        cfg = {"tour_seen": True, **saved}
        (tmp_path / "tinglongwu" / "gui.json").write_text(json.dumps(cfg), "utf-8")
        a = app.App()
        a.root.update()
        made.append(a)
        return a

    yield make
    for a in made:
        a.stop_ev.set()
        try:
            a.root.destroy()
        except tk.TclError:
            pass


def _shot(app, name):
    out = os.environ.get("TLWU_GUI_SHOTS")
    if not out:
        return
    from PIL import ImageGrab

    os.makedirs(out, exist_ok=True)
    app.root.update()
    r = app.root
    box = (r.winfo_rootx(), r.winfo_rooty(), r.winfo_rootx() + r.winfo_width(), r.winfo_rooty() + r.winfo_height())
    ImageGrab.grab(bbox=box, xdisplay=None).save(os.path.join(out, name + ".png"))  # the window, not the display


def _fill_meeting(a):
    from tinglongwu.gui.core import SPEAK_MARK

    for src, tgt, who in LINES:
        if who == "me":
            a.state.apply(("final", SPEAK_MARK + src, SPEAK_MARK + tgt))
        else:
            a.state.apply(("final", src, tgt))
    a.state.apply(("draft", "And one more thing about the sched", "還有一件關於時程的事"))
    a.render_log()


def _fill_notes(a, tmp_path):
    from tinglongwu import topics

    model = tmp_path / "m.gguf"
    model.write_bytes(b"x")
    a.settings.update(topics=True, topics_model=str(model), target="zh-tw")
    a._sync_topics_var()
    for title, points, start, end in (
        ("Q3 budget", ["Cut travel by 10%", "Launch date stays 1 Dec"], 0, 312),
        ("Hiring plan for the platform team and the long-running migration project in Taipei and Berlin",
         ["Two backend roles open", "Interviews start next week"], 312, 640),
        ("Next steps", ["Send the revised budget today"], 640, 700),
    ):
        a.topic_panel.add(topics.Topic(title, points, [], start, end))
    a.poll()
    a.root.update()


def _clipped(a, width):
    """Names of bottom-bar / key-points widgets that stick out of the window on any side."""
    left, top = a.root.winfo_rootx(), a.root.winfo_rooty()
    right, bottom = left + a.root.winfo_width(), top + a.root.winfo_height()
    bad = []
    holders = [a.bar]
    notes = getattr(a, "topics_win", None)
    if notes is not None and notes.winfo_exists():
        holders.append(notes)
    stack = list(holders)
    while stack:
        w = stack.pop()
        stack.extend(w.winfo_children())
        if not (w.winfo_ismapped() and w.winfo_width() > 1):
            continue
        x, y = w.winfo_rootx(), w.winfo_rooty()
        if x < left or y < top or x + w.winfo_width() > right or y + w.winfo_height() > bottom:
            bad.append(f"{w.winfo_class()}:{w}")
    return bad


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("state", STATES)
def test_1646_design_shot(make_app, tmp_path, state, theme, width):
    a = make_app(theme=theme, scene="meeting")
    a.root.geometry(f"{width}x{HEIGHT}+0+0")
    a.root.update()
    if state in ("meeting", "meeting_notes"):
        _fill_meeting(a)
    if state == "meeting_notes":
        _fill_notes(a, tmp_path)
    if state == "error":
        a.v_notice.set("The speech model could not be loaded. Check the Models folder and try again.")
        a.q.put(("error", "model file missing: C:\\Users\\me\\AppData\\tinglongwu\\models\\ggml-small.bin"))
        a.poll()
    a.root.update()
    assert a.settings.get("theme") == theme
    _shot(a, f"design_{state}_{theme}_{width}")
    assert _clipped(a, width) == []
