"""tkinter front end: control window with a scrolling subtitle log + optional floating subtitle bar. All widget calls happen on the main
thread; the worker thread (model load + pipeline) only talks to us through `self.q`."""
from __future__ import annotations

import os
import queue
import signal
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from ..i18n import set_ui_lang, t, ui_lang
from .icon import apply_icon, set_app_id
from ..langs import from_label as lang_from_label, label as lang_label, labels as lang_labels
from .core import (QUICK_MODES, quick_buttons, quick_settings, ALPHA_STEP, ENGINES, FONT_MAX, FONT_MIN, ROWS_MAX, ROWS_MIN, HISTORY_LINES, MODELS, PRESETS, QUALITIES, SOURCES, TARGETS, QueueDraftRenderer,
                   QueueSink, SubtitleState, clamp_font, clamp_rows, follow_newest, font_preset, load_settings,
                   combo_width, doctor_lang, is_downloaded, label_for_model, model_from_label, model_labels, rows_after_drag, run_download, run_setup_check,
                   save_settings, settings_to_argv, voice_outputs, clamp_alpha)

try:  # optional theme; the GUI works without it
    import sv_ttk
except ImportError:  # pragma: no cover
    sv_ttk = None

POLL_MS = 50
UI_LANGS = {"繁體中文": "zh-TW", "English": "en", "Русский": "ru"}
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
        w.configure(bg=BG)
        self.width = int(w.winfo_screenwidth() * 0.7)
        self.rows = clamp_rows(s.get("overlay_lines", 3))
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
        # #333: A-/A+ in the top-right corner, shown only while the pointer is over the bar
        self.hover = tk.Frame(self.box, bg=BG)
        for label, d in (("A-", -2), ("A+", 2)):
            tk.Button(self.hover, text=label, command=lambda d=d: self.bump_font(d), bd=0, padx=6, pady=0,
                      bg="#2a2a2a", fg=FG_FINAL, activebackground="#3a3a3a", activeforeground=FG_FINAL,
                      takefocus=0, cursor="hand2").pack(side="left", padx=1)
        w.bind("<Enter>", lambda _e: self.show_hover(), add="+")
        w.bind("<Leave>", lambda _e: self.win.after(60, self._leave_check), add="+")
        self.set_alpha(s.get("overlay_alpha"))
        self.bottom = None  # bottom edge kept when the bar grows (None once dragged: keep the top)
        self.set_font(s["font_size"])
        self.var_src = tk.BooleanVar(value=s["show_src"])
        self.var_pause = tk.BooleanVar(value=False)
        self.build_menu()
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

    def build_menu(self):
        """(Re)build the right-click menu in the current UI language."""
        app = self.app
        self.menu = tk.Menu(self.win, tearoff=0)
        self.menu.add_checkbutton(label=t("gui_menu_show_src"), variable=self.var_src, command=self.on_toggle_src)
        fm = tk.Menu(self.menu, tearoff=0)
        for key, d in (("gui_menu_larger", 2), ("gui_menu_smaller", -2)):
            fm.add_command(label=t(key), command=lambda d=d: self.bump_font(d))
        self.menu.add_cascade(label=t("gui_menu_font"), menu=fm)
        om = tk.Menu(self.menu, tearoff=0)
        for key, d in (("gui_menu_more_opaque", ALPHA_STEP), ("gui_menu_less_opaque", -ALPHA_STEP)):
            om.add_command(label=t(key), command=lambda d=d: self.bump_alpha(d))
        self.menu.add_cascade(label=t("gui_menu_opacity"), menu=om)
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
        lines_var = getattr(self.app, "v_lines", None)
        if lines_var is not None and lines_var.get() != str(self.rows):
            lines_var.set(str(self.rows))
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
        if size_var is not None and size_var.get() != str(n):
            size_var.set(str(n))

    def bump_font(self, d: int):
        self.set_font(self.app.settings["font_size"] + d)

    def set_alpha(self, a):
        """#333: bar opacity, clamped and stored in settings (saved to gui.json with the rest)."""
        a = clamp_alpha(a)
        self.app.settings["overlay_alpha"] = a
        try:
            self.win.attributes("-alpha", a)
        except tk.TclError:  # no compositor: the bar stays opaque, the setting is still kept
            pass

    def bump_alpha(self, d: float):
        self.set_alpha(self.app.settings["overlay_alpha"] + d)

    def show_hover(self):
        self.hover.place(relx=1.0, x=-14, y=2, anchor="ne")  # left of the scrollbar
        self.hover.lift()

    def hide_hover(self):
        self.hover.place_forget()

    def hover_shown(self) -> bool:
        return self.hover.winfo_manager() == "place"

    def _leave_check(self):
        # <Leave> also fires when the pointer moves onto a child (the buttons): hide only once it left the bar
        w = self.win
        px, py = w.winfo_pointerxy()
        x, y = w.winfo_rootx(), w.winfo_rooty()
        if not (x <= px < x + w.winfo_width() and y <= py < y + w.winfo_height()):
            self.hide_hover()

    def _font_wheel(self, d: int):
        self.bump_font(d)
        return "break"  # Ctrl+wheel resizes; it must not also scroll the history

    def on_toggle_src(self):
        self.app.settings["show_src"] = self.var_src.get()
        self.app.var_show_src.set(self.var_src.get())
        self.render(self.app.state)
        self.app.render_log()

    def on_pause(self):
        self.app.set_paused(self.var_pause.get())

    def render(self, state: SubtitleState):
        t = self.text
        top = t.index("@0,0")
        follow = self._at_end()
        show_src = self.app.settings["show_src"]
        t.configure(state="normal")
        t.delete("1.0", "end")
        rows = state.history() or [("", text, d) for text, d in self.app.placeholder()]  # "Listening..."
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
            self.win.update_idletasks()
            self.set_alpha(self.app.settings["overlay_alpha"])  # X11 drops -alpha set while the bar was never mapped
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
        self.listening = False  # a session is running (Start pressed, worker not yet stopped)
        self.load_t0: float | None = None  # #332: monotonic time of Start while models load, else None
        self.load_shown = 0
        self.stop_ev = threading.Event()
        self.worker: threading.Thread | None = None
        self.closing = False
        self.errored = False
        self.sigint = False
        set_app_id()
        self.root = tk.Tk()
        self.root.title("tinglongwu")
        apply_icon(self.root)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self._tr: list[tuple[object, str, str]] = []  # (widget, option, i18n key) re-labelled on language switch
        self.status_key: str | None = "gui_ready"
        self.quick_mode: str | None = None  # #294: first-screen button that started this session
        self.run_source = "system"
        self._build_controls()
        self._build_quick()
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
        if self.load_t0 is not None:
            self.btn.configure(text=t("gui_loading", s=self.load_shown))
        else:
            self.btn.configure(text=t("gui_stop" if self.worker is not None and self.worker.is_alive() else "gui_start"))
        self.btn_pause.configure(text=t("gui_resume" if self.state.paused else "gui_pause"))
        if self.status_key:
            self.v_status.set(t(self.status_key))
        self._sync_quick()
        self.combos["lang"].configure(values=lang_labels())
        self.v_lang.set(lang_label(lang_from_label(self.v_lang.get())))
        self.overlay.build_menu()
        self.render_log()
        if self.overlay.visible:
            self.overlay.render(self.state)

    def _combo(self, row, label, key, values):
        self._tx(ttk.Label(self.frm), label).grid(row=row, column=0, sticky="w", pady=2)
        var = tk.StringVar(value=self.settings[key])
        self.combos[key] = cb = ttk.Combobox(self.frm, textvariable=var, values=values, state="readonly", width=18)
        cb.grid(row=row, column=1, columnspan=2, sticky="we", pady=2)
        return var

    def _quality_changed(self):
        """Engine / speech model are picked for the user unless Quality is 'manual' (#81)."""
        state = "readonly" if self.v_quality.get() == "manual" else "disabled"
        for key in ("engine", "model"):
            if key in self.combos:
                self.combos[key].configure(state=state)

    def _build_controls(self):
        s = self.settings
        self.frm = f = ttk.Frame(self.root, padding=10)
        self.combos = {}
        f.pack()
        self.v_lang = self._combo(0, "gui_src_lang", "lang", lang_labels())  # ja/en/ru first, others marked (#58)
        self.v_lang.set(lang_label(self.settings["lang"]))
        self.v_target = self._combo(1, "gui_target", "target", TARGETS)
        self.v_quality = self._combo(2, "gui_quality", "quality", QUALITIES)
        self.v_engine = self._combo(3, "gui_engine", "engine", ENGINES)
        self.v_model = self._combo(4, "gui_model", "model", model_labels())
        self.v_model.set(label_for_model(self.settings["model"]))
        self.combos["model"].configure(width=combo_width(model_labels()))  # room for the "not downloaded" mark
        self.combos["model"].bind("<<ComboboxSelected>>", lambda _e: self._model_picked())
        self.downloading = None
        self.v_quality.trace_add("write", lambda *_: self._quality_changed())
        self._quality_changed()
        self.v_source = self._combo(5, "gui_source", "source", SOURCES)
        self.v_preset = self._combo(9, "gui_mode", "preset", PRESETS)
        self._tx(ttk.Label(f), "gui_glossary").grid(row=6, column=0, sticky="w", pady=2)
        self.v_gloss = tk.StringVar(value=s["glossary"])
        ttk.Entry(f, textvariable=self.v_gloss, width=22).grid(row=6, column=1, sticky="we", pady=2)
        ttk.Button(f, text="...", width=3, command=self.pick_glossary).grid(row=6, column=2, padx=(4, 0))
        self.v_save = tk.BooleanVar(value=s["save"])
        self._tx(ttk.Checkbutton(f, variable=self.v_save), "gui_save").grid(row=7, column=0, sticky="w", pady=2)
        self.v_save_path = tk.StringVar(value=s["save_path"])
        ttk.Entry(f, textvariable=self.v_save_path, width=22).grid(row=7, column=1, sticky="we", pady=2)
        ttk.Button(f, text="...", width=3, command=self.pick_save).grid(row=7, column=2, padx=(4, 0))
        self.var_show_src = tk.BooleanVar(value=s["show_src"])
        self._tx(ttk.Checkbutton(f, variable=self.var_show_src, command=self._src_toggled),
                 "gui_show_src_line").grid(row=8, column=0, sticky="w")
        self.v_live = tk.BooleanVar(value=s["live_draft"])
        self._tx(ttk.Checkbutton(f, variable=self.v_live), "gui_live_draft").grid(
            row=8, column=1, columnspan=2, sticky="w")
        self.v_voice = tk.BooleanVar(value=s["voice"])  # #330: voice into earphones, never the captured device
        self._tx(ttk.Checkbutton(f, variable=self.v_voice), "gui_voice").grid(row=17, column=0, sticky="w", pady=2)
        self.voice_default = t("gui_voice_default")
        self.v_voice_dev = tk.StringVar(value=s["voice_device"] or self.voice_default)
        ttk.Combobox(f, textvariable=self.v_voice_dev, values=[self.voice_default] + voice_outputs(),
                     state="readonly", width=18).grid(row=17, column=1, columnspan=2, sticky="we", pady=2)
        self.v_lines = self._spin(15, "gui_lines", ROWS_MIN, ROWS_MAX, s["overlay_lines"],
                                  lambda n: self.overlay.set_rows(n))
        self.v_font = self._spin(16, "gui_font_size", FONT_MIN, FONT_MAX, s["font_size"],
                                 lambda n: self.overlay.set_font(n))
        self.btn = ttk.Button(f, text=t("gui_start"), command=self.toggle)
        self.btn.grid(row=10, column=0, columnspan=2, sticky="we", pady=(8, 2))
        self.btn_pause = ttk.Button(f, text=t("gui_pause"), command=self.toggle_pause)
        self.btn_pause.grid(row=10, column=2, sticky="we", pady=(8, 2), padx=(4, 0))
        self._tx(ttk.Button(f, command=self.check_setup), "gui_check_setup").grid(
            row=13, column=0, sticky="we", pady=(6, 0))
        self._tx(ttk.Button(f, command=self.report_problem), "gui_report").grid(
            row=13, column=1, columnspan=2, sticky="we", pady=(6, 0), padx=(4, 0))
        self.v_status = tk.StringVar(value=t("gui_ready"))
        ttk.Label(f, textvariable=self.v_status, wraplength=300).grid(row=11, column=0, columnspan=3, sticky="w")
        self.v_notice = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.v_notice, wraplength=300, foreground="#b35a00").grid(
            row=12, column=0, columnspan=3, sticky="w")
        self._tx(ttk.Label(f), "gui_ui_lang").grid(row=14, column=0, sticky="w", pady=(8, 0))
        self.v_ui_lang = tk.StringVar(value=next(k for k, v in UI_LANGS.items() if v == ui_lang()))
        cb = ttk.Combobox(f, textvariable=self.v_ui_lang, values=list(UI_LANGS), state="readonly", width=12)
        cb.grid(row=14, column=1, sticky="we", pady=(8, 0))
        cb.bind("<<ComboboxSelected>>", self.switch_lang)
        if sv_ttk is not None:
            self.v_dark = tk.BooleanVar(value=self.settings.get("theme", "dark") != "light")
            self._tx(ttk.Checkbutton(f, variable=self.v_dark, command=self._toggle_theme), "gui_dark").grid(
                row=14, column=2, sticky="e", pady=(8, 0))
        self._build_log(f)

    def _build_quick(self):
        """#294 first screen: pick my language once (remembered), then one big button starts the session."""
        q = ttk.Frame(self.root, padding=(10, 10, 10, 0))
        q.pack(before=self.frm, fill="x")
        self._tx(ttk.Label(q), "gui_quick_target").grid(row=0, column=0, sticky="w")
        ttk.Combobox(q, textvariable=self.v_target, values=TARGETS, state="readonly", width=10).grid(
            row=0, column=1, sticky="we", padx=(6, 0))
        self.quick_btns = {}
        for col, mode in enumerate(QUICK_MODES):
            b = ttk.Button(q, command=lambda m=mode: self.toggle(m))
            b.grid(row=1, column=col, sticky="we", ipady=10, pady=(8, 0), padx=(0 if col == 0 else 6, 0))
            self.quick_btns[mode] = b
        q.columnconfigure(0, weight=1, uniform="quick")
        q.columnconfigure(1, weight=1, uniform="quick")
        self._sync_quick()

    def _quick_phase(self) -> str:
        if self.load_t0 is not None:
            return "loading"
        if self.downloading or (self.listening and self.stop_ev.is_set()):
            return "busy"
        return "running" if self.listening else "idle"

    def _sync_quick(self):
        """Label/enable the first-screen buttons for the current session state (#294)."""
        if not hasattr(self, "quick_btns"):
            return
        for mode, (key, enabled) in quick_buttons(self._quick_phase(), self.quick_mode).items():
            self.quick_btns[mode].configure(text=t(key, s=self.load_shown), state="normal" if enabled else "disabled")

    def _build_log(self, f):
        """Subtitles inside the main window: scrolling history, newest at the bottom (#72)."""
        bar = ttk.Frame(f)
        bar.grid(row=20, column=0, columnspan=3, sticky="we", pady=(10, 2))
        self.v_bar = tk.BooleanVar(value=self.settings["floating_bar"])
        self._tx(ttk.Checkbutton(bar, variable=self.v_bar, command=self._bar_toggled), "gui_floating_bar").pack(
            side="left")
        ttk.Button(bar, text="A+", width=3, command=lambda: self.bump_log_font(2)).pack(side="right")
        ttk.Button(bar, text="A-", width=3, command=lambda: self.bump_log_font(-2)).pack(side="right", padx=(0, 2))
        box = tk.Frame(f, width=340, height=220, bg=BG)  # fixed pixel size: a bigger font must not grow the window
        box.grid(row=21, column=0, columnspan=3, sticky="we")
        box.pack_propagate(False)
        sb = ttk.Scrollbar(box, orient="vertical")
        self.log_text = tx = tk.Text(box, bg=BG, fg=FG_FINAL, bd=0, highlightthickness=0, wrap="word", padx=8,
                                     pady=6, cursor="arrow", takefocus=0, yscrollcommand=sb.set)
        sb.configure(command=tx.yview)
        sb.pack(side="right", fill="y")
        tx.pack(side="left", fill="both", expand=True)
        tx.tag_configure("final", foreground=FG_FINAL, spacing3=4)
        tx.tag_configure("draft", foreground=FG_DRAFT, spacing3=4)
        tx.tag_configure("src", foreground=FG_SRC)
        tx.configure(state="disabled")
        tx.bind("<Control-MouseWheel>", lambda e: self.bump_log_font(2 if e.delta > 0 else -2))
        tx.bind("<Control-Button-4>", lambda e: self.bump_log_font(2))
        tx.bind("<Control-Button-5>", lambda e: self.bump_log_font(-2))
        self.set_log_font(self.settings["log_font_size"])

    def placeholder(self) -> list[tuple[str, bool]]:
        """What to show before the first subtitle of a session: the user must see that it is listening."""
        return [(t("gui_listening"), True)] if self.listening else []

    def set_log_font(self, n):
        self.settings["log_font_size"] = n = clamp_font(n, self.settings["log_font_size"])
        for tag in ("final", "draft"):
            self.log_text.tag_configure(tag, font=(FONT_FAMILY, n))
        self.log_text.tag_configure("src", font=(FONT_FAMILY, max(8, n * 3 // 4)))

    def bump_log_font(self, d: int):
        follow = self._log_at_end()
        self.set_log_font(self.settings["log_font_size"] + d)
        if follow:
            self._log_to_end()
        return "break"  # Ctrl+wheel resizes; it must not also scroll

    def _log_at_end(self) -> bool:
        # yview fractions are estimates for wrapped lines; "the last line is on screen" is exact
        return self.log_text.dlineinfo("end-1c") is not None or follow_newest(self.log_text.yview()[1])

    def _log_to_end(self):
        self.log_text.yview_moveto(1.0)

    def render_log(self):
        """Redraw the history; keep following the newest line unless the user scrolled up to re-read."""
        tx = self.log_text
        top, follow = tx.index("@0,0"), self._log_at_end()
        rows = self.state.history() or [("", text, d) for text, d in self.placeholder()]
        show_src = self.settings["show_src"]
        tx.configure(state="normal")
        tx.delete("1.0", "end")
        for i, (src, text, is_draft) in enumerate(rows):
            if show_src and src and src != text:
                tx.insert("end", src + "\n", "src")
            tx.insert("end", text + ("\n" if i < len(rows) - 1 else ""), "draft" if is_draft else "final")
        tx.configure(state="disabled")
        if follow:
            self._log_to_end()
        else:
            tx.yview(top)

    def _bar_toggled(self):
        self.settings["floating_bar"] = self.v_bar.get()
        if self.v_bar.get() and self.listening:
            self.overlay.show()
        else:
            self.overlay.hide()

    def _spin(self, row, key, lo, hi, value, apply):
        """Numeric spinbox row; each valid value applies live (empty / non-numeric / out-of-range input is ignored)."""
        self._tx(ttk.Label(self.frm), key).grid(row=row, column=0, sticky="w", pady=2)
        var = tk.StringVar(value=str(value))

        def changed(*_):
            try:
                n = int(var.get())
            except ValueError:
                return
            if lo <= n <= hi:
                apply(n)

        ttk.Spinbox(self.frm, from_=lo, to=hi, textvariable=var, width=6, command=changed).grid(
            row=row, column=1, sticky="w", pady=2)
        var.trace_add("write", changed)
        return var

    def pick_glossary(self):
        p = filedialog.askopenfilename(title=t("gui_pick_glossary"), filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if p:
            self.v_gloss.set(p)

    def _model_picked(self):
        """A model that is not on disk was picked: ask, and only on yes download it in a tlwu-setup child (#96)."""
        m = model_from_label(self.v_model.get())
        if self.downloading or is_downloaded(m):
            return
        if not messagebox.askyesno(t("gui_dl_title"), t("gui_dl_ask", model=m), parent=self.root):
            return
        self.downloading = m
        self.btn.configure(state="disabled")
        self.combos["model"].configure(state="disabled")
        self._status_text(t("gui_dl_running", model=m))
        self._sync_quick()
        threading.Thread(target=run_download, args=(m, self.q.put), daemon=True).start()

    def _download_done(self, model: str, rc: int):
        self.downloading = None
        self.btn.configure(state="disabled" if self.load_t0 is not None else "normal")  # #332: not while loading
        self._quality_changed()
        labels = model_labels()
        self.combos["model"].configure(values=labels, width=combo_width(labels))
        if model_from_label(self.v_model.get()) == model:
            self.v_model.set(label_for_model(model))
        ok = rc == 0 and is_downloaded(model)
        self._status_text(t("gui_dl_ok" if ok else "gui_dl_fail", model=model))
        self._sync_quick()

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
        self.render_log()

    def check_setup(self):
        """#313 part 2: run the `tlwu doctor` checks off the main thread; poll() shows the result."""
        self.set_status("gui_checking")
        lang = doctor_lang(lang_from_label(self.v_lang.get()))
        threading.Thread(target=run_setup_check, args=(self.q, lang), daemon=True).start()

    def _show_setup_check(self, text: str, ok: bool):
        self.set_status("gui_check_ok" if ok else "gui_check_bad")
        (messagebox.showinfo if ok else messagebox.showwarning)(t("gui_check_setup"), text, parent=self.root)

    def report_problem(self):
        from ..report import open_report
        from .core import report_info

        open_report(info=report_info(self.settings), say=lambda m: self._status_text(t("gui_open_url", url=m[:80])))

    def collect(self):
        s = self.settings
        s.update(lang=lang_from_label(self.v_lang.get()), target=self.v_target.get(), engine=self.v_engine.get(), quality=self.v_quality.get(),
                 model=model_from_label(self.v_model.get()), preset=self.v_preset.get(), glossary=self.v_gloss.get().strip(), save=self.v_save.get(),
                 save_path=self.v_save_path.get().strip(), show_src=self.var_show_src.get(),
                 live_draft=self.v_live.get(), source=self.v_source.get(), floating_bar=self.v_bar.get(),
                 voice=self.v_voice.get(),
                 voice_device="" if self.v_voice_dev.get() == self.voice_default else self.v_voice_dev.get())
        save_settings(s)

    # -- worker -------------------------------------------------------------------------------
    def toggle(self, mode: str | None = None):
        """Start / Stop. `mode` = a #294 first-screen button ("listen" / "speak"); None = the Start button."""
        if self.worker is not None and self.worker.is_alive():
            self.stop_ev.set()
            self.btn.configure(state="disabled")
            self.set_status("gui_stopping")
            self._sync_quick()
            return
        self.collect()
        run = quick_settings(self.settings, mode) if mode else dict(self.settings)
        self.quick_mode, self.run_source = mode, run.get("source")
        self.state = SubtitleState(HISTORY_LINES)
        self.set_paused(False)
        self.stop_ev = threading.Event()
        self.load_t0 = time.monotonic()  # #332: Stop stays disabled ("Loading... N s") until the models are ready
        self.load_shown = 0
        self.btn.configure(text=t("gui_loading", s=0), state="disabled")
        self.set_status("gui_starting")
        self.v_notice.set("")
        self.errored = False
        self.listening = True
        self.worker = threading.Thread(target=self._work, args=(run, self.stop_ev), daemon=True)
        self.worker.start()
        self._sync_quick()
        self.render_log()  # shows "Listening..." at once, before the first line
        if self.settings["floating_bar"]:
            self.overlay.show()

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
                    from ..cli import READY_STATUS  # already imported by the worker; not at GUI start (#332)

                    if ev[1].startswith(READY_STATUS) and self.load_t0 is not None:
                        self.load_t0 = None
                        self.btn.configure(text=t("gui_stop"), state="normal")
                        ready = "gui_ready_speak" if self.run_source == "mic" else "gui_ready_listen"
                        self._status_text(f"{t(ready)} ({ev[1]})")  # #294: visible "start playback now"
                        self._sync_quick()
                elif kind == "notice":
                    self.v_notice.set(ev[1])
                elif kind == "dl_line":
                    self._status_text(ev[1])
                elif kind == "doctor":
                    self._show_setup_check(ev[1], ev[2])
                elif kind == "dl_done":
                    self._download_done(ev[1], ev[2])
                elif kind == "error":
                    self._status_text(t("gui_error", msg=ev[1]))
                    self.errored = True
                elif kind == "stopped":
                    self.load_t0 = None
                    self.btn.configure(text=t("gui_start"), state="normal")
                    if not self.errored:
                        self.set_status("gui_stopped")
                    self.listening = False
                    self.quick_mode = None
                    self._sync_quick()
                    self.overlay.hide()
                    self.render_log()  # drops a leftover "Listening..." placeholder
                else:
                    changed |= self.state.apply(ev)
        except queue.Empty:
            pass
        if self.load_t0 is not None:  # tick the elapsed seconds so a slow CPU start never looks frozen
            secs = int(time.monotonic() - self.load_t0)
            if secs != self.load_shown:
                self.load_shown = secs
                self.btn.configure(text=t("gui_loading", s=secs))
                self._sync_quick()
        if changed and self.overlay.visible:
            self.overlay.render(self.state)
        if changed:
            self.render_log()
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
        if not paused:
            self.render_log()

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
