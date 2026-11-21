"""#611 (part 4 of #595): the model behind the GUI topic side panel, and the on/off default.

No Tk here, so it is testable headless: the widget only redraws from `TopicPanel.entries()` when the panel's
`on_change` fires. Topics arrive on the summarizer worker thread (`TopicNotes(on_topic=panel.add)`).
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

from ..topics import Topic, _mmss

# Same limit as scripts/topic_bakeoff.py MAX_P50_DELTA_PCT (#610): a model "fits" a CPU-only machine when the
# subtitle p50 grows by at most this much while it summarizes.
CPU_MAX_P50_DELTA_PCT = 10.0


def topics_default(has_cuda: bool, cpu_rows: list[dict] | None = None) -> bool:
    """Default for the topic-notes switch: on when a CUDA device is used; on CPU-only only when a #610 bake-off
    row shows a model whose subtitle p50 delta stays within the limit (no table -> off)."""
    if has_cuda:
        return True
    return any(r.get("subtitle_p50_delta_pct", float("inf")) <= CPU_MAX_P50_DELTA_PCT for r in cpu_rows or [])


NOTES_MIN_W, NOTES_MIN_H = 260, 200  # #1705: smallest floating notes window
NOTES_DEFAULT_W, NOTES_DEFAULT_H = 380, 520
NOTES_EDGE_MARGIN = 40  # default: right edge of the screen, this far in
NOTES_DEFAULT_Y = 80
NOTES_OFF_LEFT_TOP = -50  # same slack as the subtitle bar's _place_initial: a window this far off-screen is reset
NOTES_OFF_RIGHT = 50
NOTES_OFF_BOTTOM = 20


def notes_float_on(settings: dict) -> bool:
    """#1705: True when the notes live in their own floating window instead of the side panel."""
    return settings.get("notes_float") is True


def _int_or_none(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def notes_geometry(settings: dict, screen_w: int, screen_h: int) -> str:
    """#1705: Tk geometry string `WxH+X+Y` for the floating notes window from the saved size / place. Size is kept
    within [NOTES_MIN_*, screen]; a missing or off-screen place falls back to the right edge of the screen."""
    w = _int_or_none(settings.get("notes_w")) or NOTES_DEFAULT_W
    h = _int_or_none(settings.get("notes_h")) or NOTES_DEFAULT_H
    w = max(NOTES_MIN_W, min(w, max(NOTES_MIN_W, screen_w)))
    h = max(NOTES_MIN_H, min(h, max(NOTES_MIN_H, screen_h)))
    x, y = _int_or_none(settings.get("notes_x")), _int_or_none(settings.get("notes_y"))
    if (x is None or y is None or not (NOTES_OFF_LEFT_TOP <= x < screen_w - NOTES_OFF_RIGHT)
            or not (NOTES_OFF_LEFT_TOP <= y < screen_h - NOTES_OFF_BOTTOM)):
        x, y = max(0, screen_w - w - NOTES_EDGE_MARGIN), NOTES_DEFAULT_Y
    return f"{w}x{h}+{x}+{y}"


def remember_notes_geometry(settings: dict, w: int, h: int, x: int, y: int) -> bool:
    """#1705: store the floating window's size and place; a not-yet-mapped window (tiny size) is ignored.
    Returns True when the settings changed."""
    if w < NOTES_MIN_W or h < NOTES_MIN_H:
        return False
    new = {"notes_w": int(w), "notes_h": int(h), "notes_x": int(x), "notes_y": int(y)}
    if all(settings.get(k) == v for k, v in new.items()):
        return False
    settings.update(new)
    return True


class TopicPanel:
    """Thread-safe list of finished topics for the side panel."""

    def __init__(self, on_change: Callable[[], None] | None = None):
        self._lock = threading.Lock()
        self._topics: list[Topic] = []
        self.on_change = on_change
        self._busy_since: float | None = None

    def __call__(self, topic: Topic) -> None:  # TopicNotes(on_topic=panel): add() plus the state() hook below
        self.add(topic)

    def state(self, busy: bool) -> None:
        """Summarizer thread: a topic started (True) or ended (False). The model can take a minute; the panel shows it."""
        self._busy_since = time.monotonic() if busy else None
        if self.on_change is not None:
            self.on_change()

    def busy_for(self) -> float | None:
        """Seconds the current topic has been summarizing; None when idle."""
        since = self._busy_since
        return None if since is None else time.monotonic() - since

    def add(self, topic: Topic) -> None:
        with self._lock:
            self._topics.append(topic)
        if self.on_change is not None:
            self.on_change()

    def entries(self) -> list[dict]:
        """One dict per topic, oldest first: title, time range label, bullets, actions as 'who: what'."""
        with self._lock:
            return [{"title": t.title, "when": f"{_mmss(t.start)}-{_mmss(t.end)}", "bullets": list(t.bullets),
                     "actions": [f"{who}: {what}" for who, what in t.actions]} for t in self._topics]

    def clear(self) -> None:
        with self._lock:
            self._topics.clear()
        if self.on_change is not None:
            self.on_change()


def panel_hint(*, downloading: bool, has_model: bool, has_entries: bool, choice: bool | None,
               has_cuda: Callable[[], bool]) -> str:
    """#1379: i18n key of the one grey line under the meeting-notes switch ("" = none). has_cuda is only probed when
    the user never chose (choice None) and a model is there: then a CPU-only PC says why the notes are off."""
    if downloading:
        return "gui_topics_downloading"
    if has_entries:
        return ""
    if not has_model:
        return "gui_topics_no_model"
    if choice is None and not has_cuda():
        return "gui_topics_cpu_off"
    return "gui_topics_empty"
