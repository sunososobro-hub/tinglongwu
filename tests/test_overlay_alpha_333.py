"""#333 part 1: subtitle bar opacity is adjustable and kept across restarts; A-/A+ appear only on hover."""
import json
import os
import queue
import threading

import pytest

from tinglongwu import overlay
from tinglongwu.gui import core
from tinglongwu.gui.core import ALPHA_MAX, ALPHA_MIN, DEFAULTS, clamp_alpha, load_settings, save_settings


@pytest.mark.parametrize("raw,want", [(0.5, 0.5), (0.456, 0.46), (0.0, ALPHA_MIN), (-1, ALPHA_MIN), (7, ALPHA_MAX),
                                      ("0.6", 0.6), ("x", DEFAULTS["overlay_alpha"]), (None, DEFAULTS["overlay_alpha"]),
                                      (float("nan"), DEFAULTS["overlay_alpha"])])
def test_clamp_alpha(raw, want):
    assert clamp_alpha(raw) == want


def test_default_opacity_is_unchanged_from_before():
    assert DEFAULTS["overlay_alpha"] == 0.82  # the bar looked like this before #333


@pytest.mark.parametrize("saved,want", [(0.5, 0.5), (1, 1.0), (0.05, ALPHA_MIN), (True, 0.82), ("half", 0.82)])
def test_opacity_persists_across_restart(tmp_path, saved, want):
    p = tmp_path / "gui.json"
    s = load_settings(p, gpu=lambda: False)
    s["overlay_alpha"] = saved
    assert save_settings(s, p)
    assert load_settings(p, gpu=lambda: False)["overlay_alpha"] == want  # "restart"


def test_cli_overlay_saves_opacity_change(tmp_path):
    p = tmp_path / "gui.json"
    s = overlay.load(p)
    s["overlay_alpha"] = 0.4
    assert overlay.save(s, p)
    assert json.loads(p.read_text("utf-8"))["overlay_alpha"] == 0.4 and overlay.load(p)["overlay_alpha"] == 0.4


def _bar(tmp_path, alpha):
    tk = pytest.importorskip("tkinter")
    if os.name != "nt" and not os.environ.get("DISPLAY"):
        pytest.skip("no display")
    p = tmp_path / "gui.json"
    p.write_text(json.dumps({"overlay_alpha": alpha, "font_size": 28}), "utf-8")
    try:
        return overlay.Bar(queue.Queue(), threading.Event(), p)
    except tk.TclError as e:
        pytest.skip(f"no display: {e}")


def test_real_bar_applies_saved_opacity_and_steps_it(tmp_path):
    bar = _bar(tmp_path, 0.5)
    try:
        o = bar.overlay
        bar.root.update()
        assert abs(float(o.win.attributes("-alpha")) - 0.5) < 0.01
        o.bump_alpha(-0.1)
        assert bar.settings["overlay_alpha"] == 0.4 and abs(float(o.win.attributes("-alpha")) - 0.4) < 0.01
        for _ in range(20):
            o.bump_alpha(-0.1)
        assert bar.settings["overlay_alpha"] == ALPHA_MIN
        labels = [o.menu.entrycget(i, "label") for i in range(o.menu.index("end") + 1)
                  if o.menu.type(i) == "cascade"]
        assert "Opacity" in labels or "透明度" in labels or "Прозрачность" in labels
        bar.poll()
        assert json.loads(bar.path.read_text("utf-8"))["overlay_alpha"] == ALPHA_MIN  # saved on change
    finally:
        bar.root.destroy()


def test_real_bar_font_buttons_only_on_hover(tmp_path):
    bar = _bar(tmp_path, 0.82)
    try:
        o = bar.overlay
        bar.root.update()
        assert not o.hover_shown()
        o.win.event_generate("<Enter>")
        bar.root.update()
        assert o.hover_shown()
        minus, plus = o.hover.winfo_children()
        assert (minus.cget("text"), plus.cget("text")) == ("A-", "A+")
        plus.invoke()
        assert bar.settings["font_size"] == 30
        minus.invoke()
        minus.invoke()
        assert bar.settings["font_size"] == 26
        o.win.event_generate("<Motion>", warp=True, x=-500, y=-500)  # pointer leaves the bar
        bar.root.update()
        o._leave_check()
        assert not o.hover_shown()
    finally:
        bar.root.destroy()
