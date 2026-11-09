"""tkinter front end: floating subtitle bar + small control window. All widget calls happen on the main
thread; the worker thread (model load + pipeline) only talks to us through `self.q`."""
from __future__ import annotations

import os
import queue
import signal
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from .core import (ENGINES, LANGS, MODELS, SOURCES, TARGETS, QueueDraftRenderer, QueueSink, SubtitleState,
                   clamp_font, load_settings, save_settings, settings_to_argv)

POLL_MS = 50
FG_FINAL, FG_DRAFT, FG_SRC, BG = "#ffffff", "#8a8a8a", "#b8c4d6", "#101010"
FONT_FAMILY = "Microsoft JhengHei UI" if sys.platform == "win32" else "TkDefaultFont"


class Overlay:
    """Borderless always-on-top subtitle bar."""

    def __init__(self, master: tk.Tk, app: "App"):
        self.app = app
        s = app.settings
        self.win = w = tk.Toplevel(master)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        try:
            w.attributes("-alpha", 0.82)
        except tk.TclError:
            pass
        w.configure(bg=BG)
        self.width = int(w.winfo_screenwidth() * 0.7)
        tk.Frame(w, width=self.width, height=1, bg=BG).pack()  # fixes the bar width
        wrap = self.width - 24
        self.src = tk.Label(w, bg=BG, fg=FG_SRC, justify="center", wraplength=wrap)
        self.l1 = tk.Label(w, bg=BG, fg=FG_FINAL, justify="center", wraplength=wrap)
        self.l2 = tk.Label(w, bg=BG, fg=FG_FINAL, justify="center", wraplength=wrap)
        self.labels = (self.l1, self.l2)
        self.bottom = None  # bottom edge kept when the bar grows (None once dragged: keep the top)
        self.set_font(s["font_size"])
        self.menu = tk.Menu(w, tearoff=0)
        self.var_src = tk.BooleanVar(value=s["show_src"])
        self.var_pause = tk.BooleanVar(value=False)
        self.menu.add_checkbutton(label="Show source text", variable=self.var_src, command=self.on_toggle_src)
        fm = tk.Menu(self.menu, tearoff=0)
        for label, d in (("Larger (+)", 2), ("Smaller (-)", -2)):
            fm.add_command(label=label, command=lambda d=d: self.bump_font(d))
        self.menu.add_cascade(label="Font size", menu=fm)
        self.menu.add_command(label="Copy last line", command=app.copy_last)
        self.menu.add_checkbutton(label="Pause display", variable=self.var_pause, command=self.on_pause)
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=app.quit)
        for wd in (w, self.src, self.l1, self.l2):
            wd.bind("<ButtonPress-1>", self._press)
            wd.bind("<B1-Motion>", self._drag)
            wd.bind("<ButtonRelease-1>", self._release)
            wd.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
            wd.bind("<Control-MouseWheel>", lambda e: self.bump_font(2 if e.delta > 0 else -2))
            wd.bind("<Control-Button-4>", lambda e: self.bump_font(2))
            wd.bind("<Control-Button-5>", lambda e: self.bump_font(-2))
        for k in ("plus", "equal", "KP_Add"):
            w.bind(f"<KeyPress-{k}>", lambda e: self.bump_font(2))
        for k in ("minus", "KP_Subtract"):
            w.bind(f"<KeyPress-{k}>", lambda e: self.bump_font(-2))
        self._dx = self._dy = 0
        self.visible = False
        self._place_initial()

    # -- geometry -----------------------------------------------------------------------------
    def _place_initial(self):
        w = self.win
        x, y = self.app.settings.get("x"), self.app.settings.get("y")
        w.update_idletasks()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        if x is None or y is None or not (-50 <= x < sw - 50 and -50 <= y < sh - 20):
            x = (sw - self.width) // 2
            self.bottom = sh - 70  # default: bottom-centre, a little above the taskbar
            y = self.bottom - w.winfo_reqheight()
        w.geometry(f"+{x}+{y}")

    def _reanchor(self):
        self.win.update_idletasks()
        if self.bottom is not None:
            self.win.geometry(f"+{self.win.winfo_x()}+{self.bottom - self.win.winfo_reqheight()}")

    def _press(self, e):
        self._dx, self._dy = e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y()

    def _drag(self, e):
        self.bottom = None
        self.win.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _release(self, _e):
        self.app.settings["x"], self.app.settings["y"] = self.win.winfo_x(), self.win.winfo_y()

    # -- content ------------------------------------------------------------------------------
    def set_font(self, n: int):
        n = clamp_font(n)
        self.app.settings["font_size"] = n
        for lab in self.labels:
            lab.configure(font=(FONT_FAMILY, n, "bold"))
        self.src.configure(font=(FONT_FAMILY, max(9, n * 2 // 3)))

    def bump_font(self, d: int):
        self.set_font(self.app.settings["font_size"] + d)
        self._reanchor()

    def on_toggle_src(self):
        self.app.settings["show_src"] = self.var_src.get()
        self.app.var_show_src.set(self.var_src.get())
        self.render(self.app.state)

    def on_pause(self):
        self.app.paused = self.var_pause.get()
        if not self.app.paused:
            self.render(self.app.state)

    def render(self, state: SubtitleState):
        rows = state.lines()
        for lab in self.labels:
            lab.pack_forget()
        self.src.pack_forget()
        if self.app.settings["show_src"] and state.source():
            self.src.configure(text=state.source())
            self.src.pack(fill="x", padx=12, pady=(6, 0))
        for lab, (text, is_draft) in zip(self.labels, rows):
            lab.configure(text=text, fg=FG_DRAFT if is_draft else FG_FINAL)
            lab.pack(fill="x", padx=12, pady=(2, 4))
        self._reanchor()

    def show(self):
        if not self.visible:
            self.win.deiconify()
            self.win.lift()
            self.visible = True
            self.render(self.app.state)

    def hide(self):
        self.win.withdraw()
        self.visible = False


class App:
    def __init__(self):
        self.settings = load_settings()
        self.q: queue.Queue = queue.Queue()
        self.state = SubtitleState(2)
        self.stop_ev = threading.Event()
        self.worker: threading.Thread | None = None
        self.paused = False
        self.closing = False
        self.sigint = False
        self.root = tk.Tk()
        self.root.title("tinglongwu")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self._build_controls()
        self.overlay = Overlay(self.root, self)
        try:
            signal.signal(signal.SIGINT, lambda *_: setattr(self, "sigint", True))
        except ValueError:
            pass

    # -- control window -----------------------------------------------------------------------
    def _combo(self, row, label, key, values):
        ttk.Label(self.frm, text=label).grid(row=row, column=0, sticky="w", pady=2)
        var = tk.StringVar(value=self.settings[key])
        ttk.Combobox(self.frm, textvariable=var, values=values, state="readonly", width=18).grid(
            row=row, column=1, columnspan=2, sticky="we", pady=2)
        return var

    def _build_controls(self):
        s = self.settings
        self.frm = f = ttk.Frame(self.root, padding=10)
        f.pack()
        self.v_lang = self._combo(0, "Source language", "lang", LANGS)
        self.v_target = self._combo(1, "Translate to", "target", TARGETS)
        self.v_engine = self._combo(2, "Engine", "engine", ENGINES)
        self.v_model = self._combo(3, "Speech model", "model", MODELS)
        self.v_source = self._combo(4, "Audio source (system = PC sound)", "source", SOURCES)
        ttk.Label(f, text="Glossary").grid(row=5, column=0, sticky="w", pady=2)
        self.v_gloss = tk.StringVar(value=s["glossary"])
        ttk.Entry(f, textvariable=self.v_gloss, width=22).grid(row=5, column=1, sticky="we", pady=2)
        ttk.Button(f, text="...", width=3, command=self.pick_glossary).grid(row=5, column=2, padx=(4, 0))
        self.v_save = tk.BooleanVar(value=s["save"])
        ttk.Checkbutton(f, text="Save transcript", variable=self.v_save).grid(row=6, column=0, sticky="w", pady=2)
        self.v_save_path = tk.StringVar(value=s["save_path"])
        ttk.Entry(f, textvariable=self.v_save_path, width=22).grid(row=6, column=1, sticky="we", pady=2)
        ttk.Button(f, text="...", width=3, command=self.pick_save).grid(row=6, column=2, padx=(4, 0))
        self.var_show_src = tk.BooleanVar(value=s["show_src"])
        ttk.Checkbutton(f, text="Show source line", variable=self.var_show_src,
                        command=self._src_toggled).grid(row=7, column=0, sticky="w")
        self.v_live = tk.BooleanVar(value=s["live_draft"])
        ttk.Checkbutton(f, text="Live draft (uses more GPU)", variable=self.v_live).grid(
            row=7, column=1, columnspan=2, sticky="w")
        self.btn = ttk.Button(f, text="Start", command=self.toggle)
        self.btn.grid(row=8, column=0, columnspan=3, sticky="we", pady=(8, 2))
        self.v_status = tk.StringVar(value="Ready. Fully offline.")
        ttk.Label(f, textvariable=self.v_status, wraplength=300).grid(row=9, column=0, columnspan=3, sticky="w")
        self.v_notice = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.v_notice, wraplength=300, foreground="#b35a00").grid(
            row=10, column=0, columnspan=3, sticky="w")

    def pick_glossary(self):
        p = filedialog.askopenfilename(title="Glossary (.txt)", filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if p:
            self.v_gloss.set(p)

    def pick_save(self):
        p = filedialog.asksaveasfilename(title="Transcript file", defaultextension=".jsonl",
                                         filetypes=[("JSON lines", "*.jsonl"), ("All", "*.*")])
        if p:
            self.v_save_path.set(p)
            self.v_save.set(True)

    def _src_toggled(self):
        self.settings["show_src"] = self.var_show_src.get()
        self.overlay.var_src.set(self.var_show_src.get())
        self.overlay.render(self.state)

    def collect(self):
        s = self.settings
        s.update(lang=self.v_lang.get(), target=self.v_target.get(), engine=self.v_engine.get(),
                 model=self.v_model.get(), glossary=self.v_gloss.get().strip(), save=self.v_save.get(),
                 save_path=self.v_save_path.get().strip(), show_src=self.var_show_src.get(),
                 live_draft=self.v_live.get(), source=self.v_source.get())
        save_settings(s)

    # -- worker -------------------------------------------------------------------------------
    def toggle(self):
        if self.worker is not None and self.worker.is_alive():
            self.stop_ev.set()
            self.btn.configure(state="disabled")
            self.v_status.set("Stopping...")
            return
        self.collect()
        self.state = SubtitleState(2)
        self.stop_ev = threading.Event()
        self.btn.configure(text="Stop")
        self.v_status.set("Starting...")
        self.v_notice.set("")
        self.overlay.show()
        self.worker = threading.Thread(target=self._work, args=(dict(self.settings), self.stop_ev), daemon=True)
        self.worker.start()

    def _work(self, s: dict, stop: threading.Event):
        """Worker thread: no tkinter here, only self.q."""
        q = self.q
        try:
            from ..cli import parse_args, run_session
            from ..stream import DraftEngine

            a = parse_args(settings_to_argv(s))

            def draft_factory(asr, translator, segmenter, tgt):
                if not s["live_draft"] or translator is None:
                    return None
                return DraftEngine(asr, translator, QueueDraftRenderer(q), lambda: segmenter.live, tgt)

            run_session(a, sink=QueueSink(q), draft_factory=draft_factory, stop=stop,
                        status=lambda m: q.put(("status", m)),
                        notice=lambda m: q.put(("notice", m)))
        except Exception as e:  # noqa: BLE001
            q.put(("error", str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"))
        finally:
            q.put(("stopped",))

    def poll(self):
        if self.closing:
            return
        if self.sigint:
            self.quit()
            return
        changed = False
        try:
            while True:
                ev = self.q.get_nowait()
                kind = ev[0]
                if kind == "status":
                    self.v_status.set(ev[1])
                elif kind == "notice":
                    self.v_notice.set(ev[1])
                elif kind == "error":
                    self.v_status.set("Error: " + ev[1])
                elif kind == "stopped":
                    self.btn.configure(text="Start", state="normal")
                    if not self.v_status.get().startswith("Error"):
                        self.v_status.set("Stopped.")
                    self.overlay.hide()
                elif not self.paused:
                    changed |= self.state.apply(ev)
        except queue.Empty:
            pass
        if changed and self.overlay.visible:
            self.overlay.render(self.state)
        self.root.after(POLL_MS, self.poll)

    def copy_last(self):
        text = self.state.last_text()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)

    def quit(self):
        if self.closing:
            return
        self.closing = True
        self.stop_ev.set()
        try:
            self.collect()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.after(POLL_MS, self.poll)
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.quit()
        if self.worker is not None:
            self.worker.join(timeout=2.0)  # pipeline polls the stop flag every 0.25 s


def launch() -> int:
    for name in ("stdout", "stderr"):  # pythonw / gui-script: no console, streams are None
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    from ..capture._soundcard import suppress_capture_warnings

    suppress_capture_warnings()
    try:
        app = App()
    except tk.TclError as e:
        print(f"tlwu: cannot open a window ({e})", file=sys.stderr)
        return 2
    app.run()
    # native threads (CTranslate2 / llama.cpp / WASAPI) can hang interpreter teardown: leave now
    from ..cli import _hard_exit

    _hard_exit(0)
    return 0
