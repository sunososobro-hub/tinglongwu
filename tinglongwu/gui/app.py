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

from ..i18n import set_ui_lang, t, ui_lang
from .core import (ENGINES, LANGS, MODELS, PRESETS, SOURCES, TARGETS, QueueDraftRenderer, QueueSink, SubtitleState,
                   clamp_font, clamp_lines, FONT_MAX, FONT_MIN, LINES_MAX, LINES_MIN, load_settings, save_settings, settings_to_argv)

try:  # optional theme; the GUI works without it
    import sv_ttk
except ImportError:  # pragma: no cover
    sv_ttk = None

POLL_MS = 50
UI_LANGS = {"繁體中文": "zh-TW", "English": "en", "Русский": "ru"}
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
        self.wrap = wrap
        self.labels: list[tk.Label] = []
        self.bottom = None  # bottom edge kept when the bar grows (None once dragged: keep the top)
        self._ensure_labels(clamp_lines(s["lines"]))
        self.set_font(s["font_size"])
        self.var_src = tk.BooleanVar(value=s["show_src"])
        self.var_pause = tk.BooleanVar(value=False)
        self.build_menu()
        self._bind_all()
        for k in ("plus", "equal", "KP_Add"):
            w.bind(f"<KeyPress-{k}>", lambda e: self.bump_font(2))
        for k in ("minus", "KP_Subtract"):
            w.bind(f"<KeyPress-{k}>", lambda e: self.bump_font(-2))
        self._dx = self._dy = 0
        self.visible = False
        self._place_initial()

    def _ensure_labels(self, n: int):
        while len(self.labels) < n:
            lab = tk.Label(self.win, bg=BG, fg=FG_FINAL, justify="center", wraplength=self.wrap)
            lab.configure(font=(FONT_FAMILY, self.app.settings["font_size"], "bold"))
            self.labels.append(lab)
            if hasattr(self, "menu"):
                self._bind_one(lab)

    def _bind_all(self):
        for wd in (self.win, self.src, *self.labels):
            self._bind_one(wd)

    def _bind_one(self, wd):
        if True:
            wd.bind("<ButtonPress-1>", self._press)
            wd.bind("<B1-Motion>", self._drag)
            wd.bind("<ButtonRelease-1>", self._release)
            wd.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
            wd.bind("<Control-MouseWheel>", lambda e: self.bump_font(2 if e.delta > 0 else -2))
            wd.bind("<Control-Button-4>", lambda e: self.bump_font(2))
            wd.bind("<Control-Button-5>", lambda e: self.bump_font(-2))

    def build_menu(self):
        """(Re)build the right-click menu in the current UI language."""
        app = self.app
        self.menu = tk.Menu(self.win, tearoff=0)
        self.menu.add_checkbutton(label=t("gui_menu_show_src"), variable=self.var_src, command=self.on_toggle_src)
        fm = tk.Menu(self.menu, tearoff=0)
        for key, d in (("gui_menu_larger", 2), ("gui_menu_smaller", -2)):
            fm.add_command(label=t(key), command=lambda d=d: self.bump_font(d))
        self.menu.add_cascade(label=t("gui_menu_font"), menu=fm)
        self.menu.add_command(label=t("gui_menu_copy"), command=app.copy_last)
        self.menu.add_checkbutton(label=t("gui_menu_pause"), variable=self.var_pause, command=self.on_pause)
        self.menu.add_separator()
        self.menu.add_command(label=t("gui_menu_quit"), command=app.quit)

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

    def set_lines(self, n):
        n = clamp_lines(n)
        self.app.settings["lines"] = n
        self.app.state.set_max_lines(n)
        self._ensure_labels(n)
        self.render(self.app.state)

    def bump_font(self, d: int):
        self.set_font(self.app.settings["font_size"] + d)
        self._reanchor()

    def on_toggle_src(self):
        self.app.settings["show_src"] = self.var_src.get()
        self.app.var_show_src.set(self.var_src.get())
        self.render(self.app.state)

    def on_pause(self):
        self.app.set_paused(self.var_pause.get())

    def render(self, state: SubtitleState):
        rows = state.lines()
        for lab in self.labels:
            lab.pack_forget()
        self.src.pack_forget()
        if self.app.settings["show_src"] and state.source():
            self.src.configure(text=state.source())
            self.src.pack(fill="x", padx=12, pady=(6, 0))
        for lab, (text, is_draft) in zip(self.labels[:state.max_lines], rows):
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
        self.state = SubtitleState(clamp_lines(self.settings["lines"]))
        self.stop_ev = threading.Event()
        self.worker: threading.Thread | None = None
        self.closing = False
        self.errored = False
        self.sigint = False
        self.root = tk.Tk()
        self.root.title("tinglongwu")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self._tr: list[tuple[object, str, str]] = []  # (widget, option, i18n key) re-labelled on language switch
        self.status_key: str | None = "gui_ready"
        self._build_controls()
        self.overlay = Overlay(self.root, self)
        self.apply_theme(self.settings.get("theme", "dark"))
        try:
            signal.signal(signal.SIGINT, lambda *_: setattr(self, "sigint", True))
        except ValueError:
            pass

    # -- control window -----------------------------------------------------------------------
    def _tx(self, widget, key, option="text"):
        """Label `widget` with t(key) now and on every language switch."""
        widget.configure(**{option: t(key)})
        self._tr.append((widget, option, key))
        return widget

    def set_status(self, key: str):
        self.status_key = key
        self.v_status.set(t(key))

    def _status_text(self, text: str):
        self.status_key = None
        self.v_status.set(text)

    def apply_theme(self, name: str):
        self.settings["theme"] = name = "light" if name == "light" else "dark"
        if sv_ttk is not None:
            try:
                sv_ttk.set_theme(name)
            except Exception:  # noqa: BLE001
                pass

    def _toggle_theme(self):
        self.apply_theme("dark" if self.v_dark.get() else "light")

    def switch_lang(self, _e=None):
        set_ui_lang(UI_LANGS[self.v_ui_lang.get()])
        self.relabel()

    def relabel(self):
        for w, opt, key in self._tr:
            w.configure(**{opt: t(key)})
        self.btn.configure(text=t("gui_stop" if self.worker is not None and self.worker.is_alive() else "gui_start"))
        self.btn_pause.configure(text=t("gui_resume" if self.state.paused else "gui_pause"))
        if self.status_key:
            self.v_status.set(t(self.status_key))
        self.overlay.build_menu()

    def _combo(self, row, label, key, values):
        self._tx(ttk.Label(self.frm), label).grid(row=row, column=0, sticky="w", pady=2)
        var = tk.StringVar(value=self.settings[key])
        ttk.Combobox(self.frm, textvariable=var, values=values, state="readonly", width=18).grid(
            row=row, column=1, columnspan=2, sticky="we", pady=2)
        return var

    def _build_controls(self):
        s = self.settings
        self.frm = f = ttk.Frame(self.root, padding=10)
        f.pack()
        self.v_lang = self._combo(0, "gui_src_lang", "lang", LANGS)
        self.v_target = self._combo(1, "gui_target", "target", TARGETS)
        self.v_engine = self._combo(2, "gui_engine", "engine", ENGINES)
        self.v_model = self._combo(3, "gui_model", "model", MODELS)
        self.v_source = self._combo(4, "gui_source", "source", SOURCES)
        self.v_preset = self._combo(8, "gui_mode", "preset", PRESETS)
        self._tx(ttk.Label(f), "gui_glossary").grid(row=5, column=0, sticky="w", pady=2)
        self.v_gloss = tk.StringVar(value=s["glossary"])
        ttk.Entry(f, textvariable=self.v_gloss, width=22).grid(row=5, column=1, sticky="we", pady=2)
        ttk.Button(f, text="...", width=3, command=self.pick_glossary).grid(row=5, column=2, padx=(4, 0))
        self.v_save = tk.BooleanVar(value=s["save"])
        self._tx(ttk.Checkbutton(f, variable=self.v_save), "gui_save").grid(row=6, column=0, sticky="w", pady=2)
        self.v_save_path = tk.StringVar(value=s["save_path"])
        ttk.Entry(f, textvariable=self.v_save_path, width=22).grid(row=6, column=1, sticky="we", pady=2)
        ttk.Button(f, text="...", width=3, command=self.pick_save).grid(row=6, column=2, padx=(4, 0))
        self.var_show_src = tk.BooleanVar(value=s["show_src"])
        self._tx(ttk.Checkbutton(f, variable=self.var_show_src, command=self._src_toggled),
                 "gui_show_src_line").grid(row=7, column=0, sticky="w")
        self.v_live = tk.BooleanVar(value=s["live_draft"])
        self._tx(ttk.Checkbutton(f, variable=self.v_live), "gui_live_draft").grid(
            row=7, column=1, columnspan=2, sticky="w")
        self.btn = ttk.Button(f, text=t("gui_start"), command=self.toggle)
        self.btn.grid(row=9, column=0, columnspan=2, sticky="we", pady=(8, 2))
        self.btn_pause = ttk.Button(f, text=t("gui_pause"), command=self.toggle_pause)
        self.btn_pause.grid(row=9, column=2, sticky="we", pady=(8, 2), padx=(4, 0))
        self._tx(ttk.Button(f, command=self.report_problem), "gui_report").grid(
            row=12, column=0, columnspan=3, sticky="we", pady=(6, 0))
        self.v_status = tk.StringVar(value=t("gui_ready"))
        ttk.Label(f, textvariable=self.v_status, wraplength=300).grid(row=10, column=0, columnspan=3, sticky="w")
        self.v_notice = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.v_notice, wraplength=300, foreground="#b35a00").grid(
            row=11, column=0, columnspan=3, sticky="w")
        self.v_lines = self._spin(14, "gui_lines", LINES_MIN, LINES_MAX, "lines", lambda n: self.overlay.set_lines(n))
        self.v_font = self._spin(15, "gui_font_size", FONT_MIN, FONT_MAX, "font_size", self._font_changed)
        self._tx(ttk.Label(f), "gui_ui_lang").grid(row=13, column=0, sticky="w", pady=(8, 0))
        self.v_ui_lang = tk.StringVar(value=next(k for k, v in UI_LANGS.items() if v == ui_lang()))
        cb = ttk.Combobox(f, textvariable=self.v_ui_lang, values=list(UI_LANGS), state="readonly", width=12)
        cb.grid(row=13, column=1, sticky="we", pady=(8, 0))
        cb.bind("<<ComboboxSelected>>", self.switch_lang)
        if sv_ttk is not None:
            self.v_dark = tk.BooleanVar(value=self.settings.get("theme", "dark") != "light")
            self._tx(ttk.Checkbutton(f, variable=self.v_dark, command=self._toggle_theme), "gui_dark").grid(
                row=13, column=2, sticky="e", pady=(8, 0))

    def _spin(self, row, key, lo, hi, setting, apply):
        """Numeric spinbox row; every valid edit/click applies live (out-of-range or non-numeric input is ignored)."""
        self._tx(ttk.Label(self.frm), key).grid(row=row, column=0, sticky="w", pady=2)
        var = tk.StringVar(value=str(self.settings[setting]))
        ttk.Spinbox(self.frm, from_=lo, to=hi, textvariable=var, width=6, command=lambda: apply_(var.get())).grid(
            row=row, column=1, sticky="w", pady=2)

        def apply_(v):
            try:
                n = int(v)
            except ValueError:
                return
            if lo <= n <= hi:
                apply(n)

        var.trace_add("write", lambda *_: apply_(var.get()))
        return var

    def _font_changed(self, n):
        self.overlay.set_font(n)
        self.overlay._reanchor()

    def pick_glossary(self):
        p = filedialog.askopenfilename(title=t("gui_pick_glossary"), filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if p:
            self.v_gloss.set(p)

    def pick_save(self):
        p = filedialog.asksaveasfilename(title=t("gui_pick_save"), defaultextension=".jsonl",
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
                          "Engine": s["engine"]}, say=lambda m: self._status_text(t("gui_open_url", url=m[:80])))

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
            self.set_status("gui_stopping")
            return
        self.collect()
        self.state = SubtitleState(clamp_lines(self.settings["lines"]))
        self.set_paused(False)
        self.stop_ev = threading.Event()
        self.btn.configure(text=t("gui_stop"))
        self.set_status("gui_starting")
        self.v_notice.set("")
        self.errored = False
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
                    self._status_text(ev[1])
                elif kind == "notice":
                    self.v_notice.set(ev[1])
                elif kind == "error":
                    self._status_text(t("gui_error", msg=ev[1]))
                    self.errored = True
                elif kind == "stopped":
                    self.btn.configure(text=t("gui_start"), state="normal")
                    if not self.errored:
                        self.set_status("gui_stopped")
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
        self.btn_pause.configure(text=t("gui_resume" if paused else "gui_pause"))
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
