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
from tkinter import font as tkfont

from .core import (ENGINES, FONT_PRESETS, HISTORY_LINES, LANGS, MODELS, PRESETS, SOURCES, TARGETS, QueueDraftRenderer,
                   QueueSink, SubtitleState, clamp_font, clamp_rows, follow_newest, font_preset, load_settings,
                   rows_after_drag, save_settings, settings_to_argv)

POLL_MS = 50
FG_FINAL, FG_DRAFT, FG_SRC, BG = "#ffffff", "#8a8a8a", "#b8c4d6", "#101010"
FONT_FAMILY = "Microsoft JhengHei UI" if sys.platform == "win32" else "TkDefaultFont"


class Overlay:
    """Borderless always-on-top subtitle bar with scroll-back: the last HISTORY_LINES finals stay in a Text
    widget; the newest line is followed unless the user scrolled up (mouse wheel / scrollbar) to re-read."""

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
        self.rows = clamp_rows(s.get("overlay_lines", 4))
        # top edge: drag it to change how many rows are visible (the bar keeps its bottom edge)
        self.grip = tk.Frame(w, height=6, bg="#2a2a2a", cursor="sb_v_double_arrow")
        self.grip.pack(fill="x")
        self.box = tk.Frame(w, width=self.width, height=100, bg=BG)
        self.box.pack_propagate(False)
        self.box.pack()
        self.scroll = tk.Scrollbar(self.box, orient="vertical", width=10)
        self.text = tk.Text(self.box, bg=BG, fg=FG_FINAL, bd=0, highlightthickness=0, wrap="word", padx=12,
                            pady=4, cursor="arrow", takefocus=0, yscrollcommand=self.scroll.set)
        self.scroll.configure(command=self.text.yview)
        self.scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("final", foreground=FG_FINAL, justify="center", spacing3=4)
        self.text.tag_configure("draft", foreground=FG_DRAFT, justify="center", spacing3=4)
        self.text.tag_configure("src", foreground=FG_SRC, justify="center")
        self.text.configure(state="disabled")
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
        for wd in (w, self.box, self.text):
            wd.bind("<ButtonPress-1>", self._press)
            wd.bind("<B1-Motion>", self._drag)
            wd.bind("<ButtonRelease-1>", self._release)
            wd.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
            wd.bind("<Control-MouseWheel>", lambda e: self._font_wheel(2 if e.delta > 0 else -2))
            wd.bind("<Control-Button-4>", lambda e: self._font_wheel(2))
            wd.bind("<Control-Button-5>", lambda e: self._font_wheel(-2))
        self.grip.bind("<ButtonPress-1>", self._grip_press)
        self.grip.bind("<B1-Motion>", self._grip_drag)
        self.grip.bind("<ButtonRelease-1>", lambda _e: self._release(None))
        for k in ("plus", "equal", "KP_Add"):
            w.bind(f"<KeyPress-{k}>", lambda e: self.bump_font(2))
        for k in ("minus", "KP_Subtract"):
            w.bind(f"<KeyPress-{k}>", lambda e: self.bump_font(-2))
        self._dx = self._dy = 0
        self._grip_y = 0
        self._grip_rows = self.rows
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
        return "break"  # no text selection: dragging the bar moves it

    def _drag(self, e):
        self.bottom = None
        self.win.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")
        return "break"

    def _release(self, _e):
        self.app.settings["x"], self.app.settings["y"] = self.win.winfo_x(), self.win.winfo_y()
        return "break"

    def _grip_press(self, e):
        self._grip_y, self._grip_rows = e.y_root, self.rows
        self.bottom = self.win.winfo_y() + self.win.winfo_height()  # resize upwards, bottom edge stays put

    def _grip_drag(self, e):
        rows = rows_after_drag(self._grip_rows, e.y_root - self._grip_y, self._line_px())
        if rows != self.rows:
            self.set_rows(rows)

    def _line_px(self) -> int:
        return self.font.metrics("linespace") + 4  # + spacing3

    def set_rows(self, n: int):
        self.rows = clamp_rows(n)
        self.app.settings["overlay_lines"] = self.rows
        self.box.configure(height=self.rows * self._line_px() + 8)
        self._reanchor()
        if self._at_end():
            self._to_end()

    # -- content ------------------------------------------------------------------------------
    def set_font(self, n: int):
        n = clamp_font(n)
        self.app.settings["font_size"] = n
        self.font = tkfont.Font(family=FONT_FAMILY, size=n, weight="bold")
        for tag in ("final", "draft"):
            self.text.tag_configure(tag, font=self.font)
        self.text.tag_configure("src", font=(FONT_FAMILY, max(9, n * 2 // 3)))
        self.set_rows(self.rows)
        size_var = getattr(self.app, "v_font", None)
        if size_var is not None:
            size_var.set(font_preset(n))

    def bump_font(self, d: int):
        self.set_font(self.app.settings["font_size"] + d)

    def _font_wheel(self, d: int):
        self.bump_font(d)
        return "break"  # Ctrl+wheel resizes; it must not also scroll the history

    def on_toggle_src(self):
        self.app.settings["show_src"] = self.var_src.get()
        self.app.var_show_src.set(self.var_src.get())
        self.render(self.app.state)

    def on_pause(self):
        self.app.set_paused(self.var_pause.get())

    def render(self, state: SubtitleState):
        t = self.text
        top = t.index("@0,0")
        follow = self._at_end()
        show_src = self.app.settings["show_src"]
        t.configure(state="normal")
        t.delete("1.0", "end")
        rows = state.history()
        for i, (src, text, is_draft) in enumerate(rows):
            if show_src and src:
                t.insert("end", src + "\n", "src")
            t.insert("end", text + ("\n" if i < len(rows) - 1 else ""), "draft" if is_draft else "final")
        t.configure(state="disabled")
        if follow:
            self._to_end()
        else:
            t.yview(top)  # the user is reading older lines: keep the same line at the top, do not jump

    def _at_end(self) -> bool:
        # the yview fractions are estimates for wrapped lines; "is the last line on screen" is exact
        return self.text.dlineinfo("end-1c") is not None or follow_newest(self.text.yview()[1])

    def _to_end(self):
        # wrapped-line heights are only known after layout, so scroll once now and once when Tk is idle
        self.text.yview_moveto(1.0)
        self.text.after_idle(lambda: self.text.yview_moveto(1.0))

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
        self.state = SubtitleState(HISTORY_LINES)
        self.stop_ev = threading.Event()
        self.worker: threading.Thread | None = None
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
        self.v_preset = self._combo(8, "Mode (meeting = full sentences, chat = faster)", "preset", PRESETS)
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
        ttk.Label(f, text="Subtitle size").grid(row=13, column=0, sticky="w", pady=(6, 0))
        self.v_font = tk.StringVar(value=font_preset(s["font_size"]))
        cb = ttk.Combobox(f, textvariable=self.v_font, values=list(FONT_PRESETS), state="readonly", width=18)
        cb.grid(row=13, column=1, columnspan=2, sticky="we", pady=(6, 0))
        cb.bind("<<ComboboxSelected>>", lambda _e: self.overlay.set_font(FONT_PRESETS[self.v_font.get()]))
        self.btn = ttk.Button(f, text="Start", command=self.toggle)
        self.btn.grid(row=9, column=0, columnspan=2, sticky="we", pady=(8, 2))
        self.btn_pause = ttk.Button(f, text="Pause", command=self.toggle_pause)
        self.btn_pause.grid(row=9, column=2, sticky="we", pady=(8, 2), padx=(4, 0))
        ttk.Button(f, text="Report a problem", command=self.report_problem).grid(
            row=12, column=0, columnspan=3, sticky="we", pady=(6, 0))
        self.v_status = tk.StringVar(value="Ready. Fully offline.")
        ttk.Label(f, textvariable=self.v_status, wraplength=300).grid(row=10, column=0, columnspan=3, sticky="w")
        self.v_notice = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.v_notice, wraplength=300, foreground="#b35a00").grid(
            row=11, column=0, columnspan=3, sticky="w")

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

    def report_problem(self):
        from ..report import open_report

        s = self.settings
        open_report(info={"Mode": f"gui/{s['preset']}", "Language pair": f"{s['lang']} -> {s['target']}",
                          "Engine": s["engine"]}, say=lambda m: self.v_status.set("Open this URL: " + m[:80]))

    def collect(self):
        s = self.settings
        s.update(lang=self.v_lang.get(), target=self.v_target.get(), engine=self.v_engine.get(),
                 model=self.v_model.get(), preset=self.v_preset.get(), glossary=self.v_gloss.get().strip(), save=self.v_save.get(),
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
        self.state = SubtitleState(HISTORY_LINES)
        self.set_paused(False)
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
                else:
                    changed |= self.state.apply(ev)
        except queue.Empty:
            pass
        if changed and self.overlay.visible:
            self.overlay.render(self.state)
        self.root.after(POLL_MS, self.poll)

    def toggle_pause(self):
        self.set_paused(not self.state.paused)

    def set_paused(self, paused: bool):
        """Freeze/unfreeze the subtitle display (translation keeps running; resume shows the latest lines)."""
        self.state.paused = paused
        self.overlay.var_pause.set(paused)
        self.btn_pause.configure(text="Resume" if paused else "Pause")
        if not paused and self.overlay.visible:
            self.overlay.render(self.state)

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
