"""--overlay (#322): the GUI's always-on-top subtitle bar for CLI listen mode (`tlwu --overlay`).

Lines still go to the terminal; the bar shows the same finals (source + translation), is draggable, and its
position / font size / rows / show-source / opacity are kept in gui.json (the same keys the GUI bar uses). Workers only
put events on a queue; Tk is polled every POLL_MS, so a printed line is on the bar within ~POLL_MS.
Wayland compositors may ignore always-on-top (best effort). Part 1 = listen mode; the speak side is part 2."""
from __future__ import annotations

import json
import queue
import sys
import threading
import types
from pathlib import Path

from .gui.core import QueueSink, load_settings, settings_path
from .sink import Result

POLL_MS = 30  # < 100 ms from print to bar (#322 acceptance)
KEYS = ("x", "y", "font_size", "overlay_lines", "show_src", "overlay_alpha")  # what the bar changes; nothing else is written


class OverlaySink(QueueSink):
    """Wraps the terminal sink: every line still prints, and also goes to the bar's queue.
    prefix: put before each translation on the bar ('◀ ' = the other side, when speak shares the bar)."""

    def __init__(self, inner, q: queue.Queue, prefix: str = ""):
        super().__init__(q)
        self.inner, self.prefix = inner, prefix

    def write_source(self, src: str, original: str) -> None:
        self.inner.write_source(src, original)
        super().write_source(src, original)

    def write(self, r: Result) -> None:
        self.inner.write(r)
        if self.prefix:
            self.q.put(("final", r.original, self.prefix + (r.target if r.src != r.tgt and r.target else r.original)))
        else:
            super().write(r)  # same events as the GUI bar


def load(path=None) -> dict:
    return load_settings(path, gpu=lambda: False)  # gpu only decides live_draft, which the bar never writes


def save(s: dict, path=None) -> bool:
    """Merge only the bar's KEYS into gui.json; every other GUI setting in the file stays as it was."""
    p = Path(path or settings_path())
    try:
        data = json.loads(p.read_text("utf-8"))
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    data.update({k: s[k] for k in KEYS if k in s})
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        return True
    except OSError:
        return False


class Bar:
    """App-like host for gui.app.Overlay outside the GUI. Build, poll and close it on ONE thread."""

    def __init__(self, q: queue.Queue, stop: threading.Event, path=None):
        import tkinter as tk

        from .gui.app import Overlay
        from .gui.core import HISTORY_LINES, SubtitleState

        self.q, self.stop, self.path = q, stop, path
        self.settings = load(path)
        self.saved = {k: self.settings.get(k) for k in KEYS}
        self.state = SubtitleState(HISTORY_LINES)
        self.var_show_src = types.SimpleNamespace(set=lambda v: None)  # GUI checkbox mirror; none here
        self.root = tk.Tk()
        self.root.withdraw()
        self.overlay = Overlay(self.root, self)
        self.overlay.show()
        self.root.after(POLL_MS, self.poll)

    # Overlay calls these on its host
    def placeholder(self):
        from .i18n import t

        return [(t("gui_listening"), True)]

    def render_log(self):  # no log pane outside the GUI
        pass

    def set_paused(self, paused: bool):
        self.state.paused = paused
        if not paused:
            self.overlay.render(self.state)

    def copy_last(self):
        text = self.state.last_text()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)

    def quit(self):
        self.stop.set()

    def poll(self):
        changed = False
        while True:
            try:
                changed |= self.state.apply(self.q.get_nowait())
            except queue.Empty:
                break
        if changed:
            self.overlay.render(self.state)
        now = {k: self.settings.get(k) for k in KEYS}
        if now != self.saved and save(self.settings, self.path):  # saved on change: Ctrl+C exits hard
            self.saved = now
        if self.stop.is_set():
            self.root.quit()
            return
        self.root.after(POLL_MS, self.poll)

    def run(self):
        self.root.mainloop()
        self.root.destroy()


def run_listen(work, path=None, join_s: float = 5.0) -> int:
    """Tk on the main thread, work(q, stop) (the pipeline: listen, or speak in part 2) on a worker; the bar's
    Quit, Ctrl+C or the end of the pipeline stops both. An int returned by work is the exit code. After the bar
    closes the worker gets up to join_s to finish its own cleanup (speak: TTS subprocess, confirm prompt)."""
    import signal

    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    try:
        bar = Bar(q, stop, path)
    except Exception as e:  # noqa: BLE001 - no tkinter / no display: say so and stop
        print(f"tlwu: --overlay needs a desktop session with tkinter: {e}", file=sys.stderr)
        return 2
    rc = [0]

    def body():
        try:
            r = work(q, stop)
            if isinstance(r, int):
                rc[0] = r
        except SystemExit as e:  # argparse error / missing model inside the worker
            if not isinstance(e.code, int) and e.code:
                print(f"tlwu: {e.code}", file=sys.stderr, flush=True)
            rc[0] = e.code if isinstance(e.code, int) else 2
        except BaseException as e:  # noqa: BLE001
            print(f"tlwu: {e}", file=sys.stderr, flush=True)
            rc[0] = 2
        finally:
            stop.set()

    try:
        prev = signal.signal(signal.SIGINT, lambda *_: stop.set())
    except ValueError:
        prev = None
    th = threading.Thread(target=body, name="tlwu-listen", daemon=True)
    th.start()
    try:
        bar.run()
    finally:
        stop.set()
        th.join(join_s)
        if prev is not None:
            signal.signal(signal.SIGINT, prev)
    return rc[0]
