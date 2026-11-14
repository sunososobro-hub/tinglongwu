"""tkinter front end: control window with a scrolling subtitle log + optional floating subtitle bar. All widget calls happen on the main
thread; the worker thread (model load + pipeline) only talks to us through `self.q`."""
from __future__ import annotations

from .. import diag as _diag

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
from .core import (cpu_ram_gb, QUICK_MODES, SPEAK_HINT_CABLE, meeting_cable, quick_buttons, quick_settings, SEND_MODES, send_mode_notice_key, speak_route, speak_start_view, tour_on_start, tour_text, ALPHA_STEP, ENGINES, FONT_MAX, FONT_MIN, ROWS_MAX, ROWS_MIN, HISTORY_LINES, MODELS, PRESETS, QUALITIES, SOURCES, TARGETS, QueueDraftRenderer,
                   QueueSink, SubtitleState, engine_from_label, engine_labels, clamp_font, clamp_rows, follow_newest, font_preset, load_settings,
                   combo_width, doctor_lang, is_downloaded, label_for_model, model_from_label, model_labels, rows_after_drag, run_download, run_setup_check,
                   save_settings, settings_to_argv, voice_outputs, clamp_alpha, run_pack_download)
from . import autoupdate, pack_on_demand
from .. import __version__, userdirs

try:  # optional theme; the GUI works without it
    import sv_ttk
except ImportError:  # pragma: no cover
    sv_ttk = None

POLL_MS = 50
UPDATE_CHECK_DELAY_MS = 5000
UPDATE_LINE_WRAP_PX = 200  # #455: text + two buttons stay inside the 420 px main window
UPDATE_ROW = 13  # #756: grid row of the Check setup / Report / update controls; must stay above the log row
UPDATE_QUIT_DELAY_MS = 1500  # #698: let the "Updating..." line paint before the window closes
UNINSTALL_URI = "ms-settings:appsfeatures"  # #738: File > Uninstall opens Windows "Installed apps"
FONT_MENU_SIZES = tuple(range(FONT_MIN, FONT_MAX + 1, 4))  # #738: Settings > Font size choices (12, 16, ... 72)
UI_LANGS = {"繁體中文": "zh-TW", "English": "en", "Русский": "ru"}
FG_FINAL, FG_DRAFT, FG_SRC, BG = "#ffffff", "#8a8a8a", "#b8c4d6", "#101010"
FONT_FAMILY = "Microsoft JhengHei UI" if sys.platform == "win32" else "TkDefaultFont"
# #455: control-bar status dot: idle / models loading / listening (fully offline) / stopped on an error
DOT = {"idle": "#8a8a8a", "loading": "#e0a000", "on": "#2ea043", "error": "#d0453a"}


def open_target(target: str) -> None:
    """Open a folder or URI with the OS handler (#738: log folder, Windows "Installed apps")."""
    import subprocess

    try:
        if sys.platform == "win32":
            os.startfile(target)  # noqa: S606 - a folder or the ms-settings: URI
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", target])
    except OSError:
        _diag.swallowed(__name__)


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
        if hasattr(app, "show_tour"):  # #314: the GUI only; the CLI subtitle bar (Bar) reuses this menu
            self.menu.add_command(label=t("gui_menu_tour"), command=app.show_tour)
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


class Tour:
    """#314 first-launch overlay: 3 numbered steps placed under the control bar, step 1 holds the real
    source-language box, step 3 is shown inside the real subtitle bar. "Got it" closes it for good; "?" reopens it."""

    def __init__(self, app: "App"):
        self.app = app
        self.win = w = tk.Toplevel(app.root)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        self.frm = f = ttk.Frame(w, padding=10, relief="solid", borderwidth=1)
        f.pack()
        self.title = ttk.Label(f, font=(FONT_FAMILY, 11, "bold"))
        self.title.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.step1 = ttk.Label(f)
        self.step1.grid(row=1, column=0, sticky="w")
        self.lang_box = ttk.Combobox(f, textvariable=app.v_lang, values=lang_labels(), state="readonly", width=14)
        self.lang_box.grid(row=1, column=1, sticky="we", padx=(6, 0))
        self.step2 = ttk.Label(f)
        self.step2.grid(row=2, column=0, columnspan=2, sticky="w", pady=4)
        self.step3 = ttk.Label(f)
        self.step3.grid(row=3, column=0, columnspan=2, sticky="w")
        self.btn_ok = ttk.Button(f, command=app.tour_done)
        self.btn_ok.grid(row=4, column=1, sticky="e", pady=(8, 0))
        self.visible = False
        from ..i18n import _system_tag

        self.system_tag = _system_tag()  # zh-CN vs zh-TW text; the OS locale does not change while the GUI runs
        self.relabel()

    def text(self) -> dict[str, str]:
        return tour_text(ui_lang(), self.system_tag)

    def relabel(self):
        tx = self.text()
        for widget, key in ((self.title, "title"), (self.step1, "step1"), (self.step2, "step2"),
                            (self.step3, "step3"), (self.btn_ok, "ok")):
            widget.configure(text=tx[key])
        self.lang_box.configure(values=lang_labels())

    def show(self):
        r = self.app.root
        r.update_idletasks()
        self.win.update_idletasks()
        x, y = r.winfo_rootx(), r.winfo_rooty() + r.winfo_height() + 4
        x = max(0, min(x, r.winfo_screenwidth() - self.win.winfo_reqwidth()))  # keep the whole overlay on screen
        if y + self.win.winfo_reqheight() > r.winfo_screenheight():  # bar near the bottom: open above it
            y = max(0, r.winfo_rooty() - self.win.winfo_reqheight() - 4)
        self.win.geometry(f"+{x}+{y}")
        self.win.deiconify()
        self.win.lift()
        self.visible = True

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
        self.speak_hint: str | None = None  # #710: speak_route() result for this session
        self._build_controls()
        self._build_quick()
        self._build_bar()
        self._build_update_line()
        self.build_menubar()
        self.root.attributes("-topmost", True)  # #455: the small bar stays reachable over the meeting window
        self.overlay = Overlay(self.root, self)
        self.tour = Tour(self)
        self.apply_theme(self.settings.get("theme", "dark"))
        if tour_on_start(self.settings):
            self.show_tour()
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
                _diag.swallowed(__name__)
                pass
        st = ttk.Style(self.root)  # #455: tighter buttons keep the control bar within 420 px (per theme)
        for style in ("Bar.TButton", "Bar.Toolbutton"):
            st.configure(style, padding=(4, 1))

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
        self._adv_toggled()
        self._send_mode_changed()  # #834: the notice follows the UI language
        if self.status_key:
            self.v_status.set(t(self.status_key))
        self._sync_quick()
        self.combos["lang"].configure(values=lang_labels())
        self.home_lang.configure(values=lang_labels())
        self.v_lang.set(lang_label(lang_from_label(self.v_lang.get())))
        self.overlay.build_menu()
        self.build_menubar()
        self._upd_relabel()
        self.tour.relabel()
        self.render_log()
        if self.overlay.visible:
            self.overlay.render(self.state)

    def _build_send_mode(self, f):
        """#834: speak send mode (default confirm); countdown / auto show the quality notice."""
        box = ttk.Frame(f)
        box.grid(row=3, column=0, columnspan=3, sticky="we")
        self._tx(ttk.Label(box), "gui_send_mode").grid(row=0, column=0, sticky="w", pady=2)
        self.v_send_mode = tk.StringVar(value=self.settings["send_mode"])
        ttk.Combobox(box, textvariable=self.v_send_mode, values=list(SEND_MODES), state="readonly",
                     width=18).grid(row=0, column=1, sticky="we", pady=2, padx=(6, 0))
        self.lbl_send_notice = ttk.Label(box, wraplength=300, justify="left")
        self.lbl_send_notice.grid(row=1, column=0, columnspan=2, sticky="w")
        self.v_send_mode.trace_add("write", lambda *_: self._send_mode_changed())
        self._send_mode_changed()

    def _send_mode_changed(self):
        mode = self.v_send_mode.get()
        if mode in SEND_MODES:
            self.settings["send_mode"] = mode
        key = send_mode_notice_key(mode)
        self.lbl_send_notice.configure(text=t(key) if key else "")

    def _combo(self, row, label, key, values):
        self._tx(ttk.Label(self.frm), label).grid(row=row, column=0, sticky="w", pady=2)
        var = tk.StringVar(value=self.settings[key])
        self.combos[key] = cb = ttk.Combobox(self.frm, textvariable=var, values=values, state="readonly", width=18)
        cb.grid(row=row, column=1, columnspan=2, sticky="we", pady=2)
        return var

    def set_advanced(self, on: bool):
        """#399: show / hide the engine dropdown; the choice is saved with the other GUI settings."""
        self.v_adv.set(bool(on))
        self._adv_toggled()

    def _adv_toggled(self):
        on = self.v_adv.get()
        self.settings["advanced_open"] = on
        if on:  # #737: the whole settings block (engine included) lives under Advanced
            self.frm.pack(after=self.adv_btn, anchor="w")
        else:
            self.frm.pack_forget()
        self.adv_btn.configure(text=t("gui_advanced_open" if on else "gui_advanced"))
        if on:
            self.combos["engine"].configure(values=engine_labels())

    def _quality_changed(self):
        """Engine / speech model are picked for the user unless Quality is 'manual' (#81)."""
        state = "readonly" if self.v_quality.get() == "manual" else "disabled"
        for key in ("engine", "model"):
            if key in self.combos:
                self.combos[key].configure(state=state)

    def _build_bar(self):
        """#455: the main window is only this bar; every setting is in the "⚙ Adjust" panel."""
        b = self.bar = ttk.Frame(self.root, padding=2)
        b.pack()
        self.v_mode = tk.StringVar(value="listen")  # Speak = the #294 "I speak" session (mic, my language out)
        for key, val in (("gui_listen", "listen"), ("gui_speak", "speak")):
            self._tx(ttk.Radiobutton(b, variable=self.v_mode, value=val, style="Bar.Toolbutton", width=0),
                     key).pack(side="left")
        self.v_pair = tk.StringVar()
        ttk.Label(b, textvariable=self.v_pair).pack(side="left", padx=2)
        for v in (self.v_lang, self.v_target, self.v_mode):
            v.trace_add("write", lambda *_: self._pair_changed())
        self._pair_changed()
        self.btn = ttk.Button(b, text=t("gui_start"), command=self._bar_start, style="Bar.TButton", width=0)
        self.btn.pack(side="left")
        self.dot = tk.Canvas(b, width=12, height=12, highlightthickness=0, bd=0)
        self.dot_oval = self.dot.create_oval(1, 1, 11, 11, outline="")
        self.dot.pack(side="left", padx=2)
        self.set_dot("idle")
        self.btn_adjust = self._tx(ttk.Button(b, command=self.toggle_adjust, style="Bar.TButton", width=0), "gui_adjust")
        self.btn_adjust.pack(side="left")

    # -- #314 first-launch overlay ---------------------------------------------------------------
    def show_tour(self):
        """Open the 3-step overlay; the subtitle bar is shown with step 3 in it so the user sees where to look."""
        self.tour.relabel()
        self.tour.show()
        self.overlay.show()
        self.overlay.render(self.state)

    def tour_done(self):
        """"Got it": close for good (saved now, so a crash later still counts) and keep the subtitle bar on, as
        step 3 promised the subtitles appear there."""
        self.tour.hide()
        self.settings["tour_seen"] = True
        self.v_bar.set(True)
        self.collect()  # saves gui.json with tour_seen + floating_bar
        if self.listening:
            self.overlay.render(self.state)
        else:
            self.overlay.hide()

    def _bar_start(self):
        """Listen = a session with the saved settings (as the old Start); Speak = the #294 speak session."""
        self.toggle("speak" if self.v_mode.get() == "speak" else None)

    def _pair_changed(self):
        """The direction the next Start runs: Speak flips it the #294 way (my language -> the other one)."""
        s = {"lang": lang_from_label(self.v_lang.get()), "target": self.v_target.get()}
        if self.v_mode.get() == "speak":
            s = quick_settings(s, "speak")
        self.v_pair.set(f"{s['lang']}→{s['target']}")

    def set_dot(self, name: str):
        self.dot_state = name
        self.dot.itemconfigure(self.dot_oval, fill=DOT[name])

    def _show_panel(self):
        """Open and raise the Adjust panel (the status and notice lines live there)."""
        self.panel.deiconify()
        self.panel.lift()

    def toggle_adjust(self):
        if self.panel.state() == "normal":
            self.panel.withdraw()
        else:
            self.panel.deiconify()
            self.panel.lift()

    def _build_controls(self):
        s = self.settings
        self.panel = p = tk.Toplevel(self.root)  # #455: the "⚙ Adjust" panel, hidden until the bar asks for it
        p.withdraw()
        p.title("tinglongwu")
        p.resizable(False, False)
        p.protocol("WM_DELETE_WINDOW", p.withdraw)
        self.frm = f = ttk.Frame(p, padding=10)
        self.combos = {}
        self.v_lang = self._combo(0, "gui_src_lang", "lang", lang_labels())  # ja/en/ru first, others marked (#58)
        self.v_lang.set(lang_label(self.settings["lang"]))
        self.v_target = self._combo(1, "gui_target", "target", TARGETS)
        self.v_quality = self._combo(2, "gui_quality", "quality", QUALITIES)
        self.v_engine = self._combo(19, "gui_engine", "engine", engine_labels())  # #399: under Advanced (rows 18-19: toggle, engine)
        self.v_engine.set(next((l for l in engine_labels() if engine_from_label(l) == s["engine"]), s["engine"]))
        self.v_adv = tk.BooleanVar(value=False)
        self.v_model = self._combo(4, "gui_model", "model", model_labels(ram_gb=self._cpu_ram()))
        self.v_model.set(label_for_model(self.settings["model"], ram_gb=self._cpu_ram()))
        self.combos["model"].configure(width=combo_width(model_labels(ram_gb=self._cpu_ram())))  # room for the "not downloaded" mark
        self.combos["model"].bind("<<ComboboxSelected>>", lambda _e: self._model_picked())
        self.downloading = None
        self.v_quality.trace_add("write", lambda *_: self._quality_changed())
        self._quality_changed()
        self.v_source = self._combo(5, "gui_source", "source", SOURCES)
        self._build_send_mode(f)
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
        outs = voice_outputs()
        shown = s["voice_device"] if s["voice_device"] in outs or not outs else ""  # #448: unplugged -> default
        self.v_voice_dev = tk.StringVar(value=shown or self.voice_default)
        ttk.Combobox(f, textvariable=self.v_voice_dev, values=[self.voice_default] + outs,
                     state="readonly", width=18).grid(row=17, column=1, columnspan=2, sticky="we", pady=2)
        # #738: subtitle lines / font size, UI language, dark theme, updates, report, about live in the menu bar
        self.v_lines = self._num_var(ROWS_MIN, ROWS_MAX, s["overlay_lines"], lambda n: self.overlay.set_rows(n))
        self.v_font = self._num_var(FONT_MIN, FONT_MAX, s["font_size"], lambda n: self.overlay.set_font(n))
        self.btn_pause = ttk.Button(f, text=t("gui_pause"), command=self.toggle_pause)  # Start/Stop is on the bar
        self.btn_pause.grid(row=10, column=0, columnspan=3, sticky="we", pady=(8, 2))
        # Check setup stays in the panel, above the subtitle log (#756); Report a problem and the update controls
        # moved to the menu bar's Help menu (#738)
        self.act_row = act = ttk.Frame(f)
        act.grid(row=UPDATE_ROW, column=0, columnspan=3, sticky="we", pady=(6, 0))
        act.columnconfigure(0, weight=1)
        self._tx(ttk.Button(act, command=self.check_setup), "gui_check_setup").grid(row=0, column=0, sticky="we")
        self.v_auto_upd = tk.BooleanVar(value=autoupdate.auto_on(userdirs.read_config()))
        self.v_status = tk.StringVar(value=t("gui_ready"))
        self.status_box = sb = ttk.Frame(p, padding=(10, 6, 10, 0))  # #737: status/notice stay visible, collapsed
        ttk.Label(sb, textvariable=self.v_status, wraplength=300).pack(anchor="w")
        self.v_notice = tk.StringVar(value="")
        ttk.Label(sb, textvariable=self.v_notice, wraplength=300, foreground="#b35a00").pack(anchor="w")
        self.v_ui_lang = tk.StringVar(value=next(k for k, v in UI_LANGS.items() if v == ui_lang()))
        if sv_ttk is not None:
            self.v_dark = tk.BooleanVar(value=self.settings.get("theme", "dark") != "light")
        self._build_log(f)

    # -- #698 auto-update -------------------------------------------------------------------------
    def _build_update_line(self):
        """One non-blocking line under the bar: "New version ... [Update now] [Later]". Hidden until needed."""
        self.upd_ver = None  # newer version seen this run
        self.upd_exe = None  # verified installer path once downloaded
        self.upd_busy = False  # a check or download child is running
        self.upd_msg: tuple | None = None  # (i18n key, format kwargs, button keys)
        u = self.upd_frame = ttk.Frame(self.root, padding=(4, 0, 4, 2))
        self.v_upd = tk.StringVar()
        ttk.Label(u, textvariable=self.v_upd, wraplength=UPDATE_LINE_WRAP_PX).pack(side="left")
        self.upd_no = ttk.Button(u, command=self._update_later, style="Bar.TButton", width=0)
        self.upd_yes = ttk.Button(u, command=self._update_yes, style="Bar.TButton", width=0)
        self.upd_apply_when_ready = False

    def _upd_show(self, key: str, buttons: tuple[str, str] | None = None, **kw):
        self.upd_msg = (key, kw, buttons)
        self.v_upd.set(t(key, **kw))
        self.upd_yes.pack_forget()
        self.upd_no.pack_forget()
        if buttons:
            self.upd_yes.configure(text=t(buttons[0]))
            self.upd_no.configure(text=t(buttons[1]))
            self.upd_no.pack(side="right")
            self.upd_yes.pack(side="right", padx=(4, 2))
        if not self.upd_frame.winfo_ismapped():
            self.upd_frame.pack(fill="x")

    def _upd_relabel(self):
        if self.upd_msg:
            key, kw, buttons = self.upd_msg
            self._upd_show(key, buttons, **kw)

    def _update_later(self):
        self.upd_msg = None
        self.upd_frame.pack_forget()

    def _auto_update_check(self):
        if not autoupdate.is_wizard_install():
            return
        if autoupdate.should_auto_check(userdirs.read_config()):
            autoupdate.mark_checked()
            self._check_update(manual=False)

    def _check_update(self, manual: bool):
        if self.upd_busy:
            return
        if self.upd_exe:  # already downloaded this run
            self._upd_show("gui_upd_ready", ("gui_upd_restart", "gui_upd_later"), ver=self.upd_ver)
            return
        self.upd_busy = True
        if manual:
            self.set_status("gui_checking")
        threading.Thread(target=autoupdate.run_check, args=(__version__, self.q.put, manual), daemon=True).start()

    def _update_found(self, ver: str, manual: bool):
        self.upd_busy = False
        self.upd_ver = ver
        if manual:
            self.set_status("gui_ready")
        if autoupdate.auto_on(userdirs.read_config()) and not manual:
            self._download_update(silent=True)  # auto_update=1: fetch quietly, ask only "Restart now?"
        else:
            self._upd_show("gui_upd_available", ("gui_upd_now", "gui_upd_later"), ver=ver)

    def _update_yes(self):
        if self.upd_exe:
            self._apply_update()
        elif self.upd_ver and not self.upd_busy:
            self._download_update(silent=False)

    def _download_update(self, silent: bool):
        self.upd_busy = True
        self.upd_apply_when_ready = not silent  # "Update now" clicked: install as soon as it is verified
        if not silent:
            self._upd_show("gui_upd_downloading", ver=self.upd_ver, pct=0)
        threading.Thread(target=autoupdate.run_download, args=(self.q.put,), daemon=True).start()

    def _update_event(self, ev) -> bool:
        """Handle an upd_* queue event; False when `ev` is not one."""
        kind = ev[0]
        if kind == "upd_found":
            self._update_found(ev[1], ev[2])
        elif kind == "upd_none":
            self.upd_busy = False
            if ev[1]:
                self._status_text(t("gui_upd_none", ver=__version__))
        elif kind == "upd_progress":
            if self.upd_msg and self.upd_msg[0] == "gui_upd_downloading":
                self._upd_show("gui_upd_downloading", ver=self.upd_ver, pct=ev[1])
        elif kind == "upd_ready":
            self.upd_busy = False
            self.upd_exe = ev[1]
            if self.upd_apply_when_ready:
                self._apply_update()
            else:
                self._upd_show("gui_upd_ready", ("gui_upd_restart", "gui_upd_later"), ver=self.upd_ver)
        elif kind == "upd_fail":
            self.upd_busy = False
            if self.upd_apply_when_ready:  # silent background failures stay silent
                self._upd_show("gui_upd_fail", ("gui_upd_now", "gui_upd_later"), why=ev[1])
        else:
            return False
        return True

    def _apply_update(self):
        self._upd_show("gui_upd_applying")
        self.root.update_idletasks()
        try:
            autoupdate.start_apply(self.upd_exe)
        except Exception as e:  # noqa: BLE001
            self._upd_show("gui_upd_fail", ("gui_upd_now", "gui_upd_later"), why=type(e).__name__)
            return
        self.root.after(UPDATE_QUIT_DELAY_MS, self.quit)  # the helper waits for this process, then installs and restarts the GUI

    # -- #738 menu bar: File / Settings / Help hold the rarely used actions ------------------------------------
    def build_menubar(self):
        """(Re)build the window's menu bar in the current UI language."""
        mb = tk.Menu(self.root, tearoff=0)
        fm = tk.Menu(mb, tearoff=0)
        fm.add_command(label=t("gui_menu_logs"), command=self.open_log_folder)
        fm.add_command(label=t("gui_menu_uninstall"), command=self.open_uninstall)
        fm.add_separator()
        fm.add_command(label=t("gui_menu_quit"), command=self.quit)
        mb.add_cascade(label=t("gui_menu_file"), menu=fm)
        sm = tk.Menu(mb, tearoff=0)
        lm = tk.Menu(sm, tearoff=0)
        for name in UI_LANGS:
            lm.add_radiobutton(label=name, value=name, variable=self.v_ui_lang, command=self.switch_lang)
        sm.add_cascade(label=t("gui_ui_lang"), menu=lm)
        if sv_ttk is not None:
            sm.add_checkbutton(label=t("gui_dark"), variable=self.v_dark, command=self._toggle_theme)
        rm = tk.Menu(sm, tearoff=0)
        for n in range(ROWS_MIN, ROWS_MAX + 1):
            rm.add_radiobutton(label=str(n), value=str(n), variable=self.v_lines)
        sm.add_cascade(label=t("gui_lines"), menu=rm)
        zm = tk.Menu(sm, tearoff=0)
        for n in FONT_MENU_SIZES:
            zm.add_radiobutton(label=str(n), value=str(n), variable=self.v_font)
        sm.add_cascade(label=t("gui_font_size"), menu=zm)
        mb.add_cascade(label=t("gui_menu_settings"), menu=sm)
        hm = tk.Menu(mb, tearoff=0)
        hm.add_command(label=t("gui_menu_tour"), command=self.show_tour)
        if autoupdate.is_wizard_install():  # #698: git checkouts keep update.bat
            hm.add_command(label=t("gui_upd_check"), command=lambda: self._check_update(manual=True))
            hm.add_checkbutton(label=t("gui_upd_auto"), variable=self.v_auto_upd,
                               command=lambda: autoupdate.set_auto(self.v_auto_upd.get()))
        hm.add_command(label=t("gui_report"), command=lambda: self.report_problem())
        hm.add_separator()
        hm.add_command(label=t("gui_menu_about"), command=self.show_about)
        mb.add_cascade(label=t("gui_menu_help"), menu=hm)
        old = getattr(self, "menubar", None)
        self.menubar = mb
        self.root.configure(menu=mb)
        if old is not None:
            old.destroy()

    def open_log_folder(self):
        d = userdirs.log_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            _diag.swallowed(__name__)
        open_target(str(d))

    def open_uninstall(self):
        if sys.platform == "win32":
            open_target(UNINSTALL_URI)  # Windows Settings > Apps > Installed apps
        else:
            messagebox.showinfo(t("gui_menu_uninstall"), t("gui_uninstall_other"), parent=self.root)

    def show_about(self):
        messagebox.showinfo(t("gui_menu_about"), t("gui_about_text", ver=__version__), parent=self.root)

    def _build_quick(self):
        """#294 easy start: pick my language once (remembered), then one big button starts the session.
        #455: lives at the top of the Adjust panel; the bar's [Listen | Speak] + Start does the same in one place."""
        q = ttk.Frame(self.panel, padding=(10, 10, 10, 0))
        q.pack(fill="x")
        self.status_box.pack(fill="x")
        # #737: everything else sits under this toggle, collapsed by default (state remembered as advanced_open)
        self.adv_btn = ttk.Checkbutton(self.panel, variable=self.v_adv, style="Toolbutton", command=self._adv_toggled)
        self.adv_btn.pack(anchor="w", padx=10, pady=(6, 0))
        self.set_advanced(bool(self.settings["advanced_open"]))
        # #737: the one picker on the first screen is the OTHER side's language (= source language `lang`, the same
        # variable as tour step 1); my language (`target`) is under Advanced. quick_settings() turns it into
        # listen: other -> mine, speak: mine -> other.
        self._tx(ttk.Label(q), "gui_quick_other").grid(row=0, column=0, sticky="w")
        self.home_lang = ttk.Combobox(q, textvariable=self.v_lang, values=lang_labels(), state="readonly", width=14)
        self.home_lang.grid(row=0, column=1, sticky="we", padx=(6, 0))
        # #314 "How to start (?)" is in the menu bar's Help menu (#738) and in the subtitle bar's right-click menu
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
        if self.listening:
            return [(t("gui_listening"), True)]
        tour = getattr(self, "tour", None)
        return [(tour.text()["step3"], True)] if tour is not None and tour.visible else []  # #314 step 3

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

    def _num_var(self, lo, hi, value, apply):
        """Numeric setting (menu bar radio items, #738); each valid value applies live (empty / non-numeric /
        out-of-range values are ignored)."""
        var = tk.StringVar(value=str(value))

        def changed(*_):
            try:
                n = int(var.get())
            except ValueError:
                return
            if lo <= n <= hi:
                apply(n)

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

    def _offer_pack(self, run: dict, mode) -> bool:
        """#673: True when a pack is missing (dialog shown / download running) and this Start must not go on now.
        "Not now" changes nothing (mode and language stay as they were)."""
        from ..models import models_dir

        try:
            need = pack_on_demand.first_missing(run.get("lang"), run.get("target"), models_dir())
        except Exception:  # noqa: BLE001  # no setup package / unreadable manifest: behave as before
            return False
        if need is None or self.downloading:
            return self.downloading is not None and need is not None
        pack, mb = need
        if not messagebox.askyesno(t("gui_dl_title"), pack_on_demand.dialog_text(ui_lang(), pack, mb), parent=self.root):
            return True
        self.downloading = pack
        self.pack_retry = mode
        self.btn.configure(state="disabled")
        self._status_text(t("gui_dl_running", model=pack))
        self.panel.deiconify()  # the status line lives in the Adjust panel: show it so progress is visible
        self.panel.lift()
        self._sync_quick()
        threading.Thread(target=run_pack_download, args=(pack, self.q.put), daemon=True).start()
        return True

    def _offer_cable(self) -> bool:
        """#804: True when this speak Start must not go on now: the VB-CABLE question was answered "no" (back to
        listen) or the download + elevated install is running (Start resumes in _cable_done)."""
        from .. import vbcable

        if self.downloading or not vbcable.needs_offer(vbcable.output_names()):
            return False
        lang = ui_lang()
        if not messagebox.askyesno(vbcable.title_text(lang), vbcable.dialog_text(lang), parent=self.root):
            self.v_mode.set("listen")
            return True
        self.downloading = "VB-CABLE"
        self.btn.configure(state="disabled")
        self._status_text(t("gui_dl_running", model="VB-CABLE"))
        self._sync_quick()
        threading.Thread(target=lambda: self.q.put(("cable_done", vbcable.install_flow(
            vbcable.child_download, vbcable.child_install))), daemon=True).start()
        return True

    def _cable_done(self, res: str):
        """#804: "installed" + CABLE now listed -> 3-step help, then the speak Start goes on; anything else -> listen
        mode (no pin yet / offline: the official page opens so the user can install by hand)."""
        from .. import vbcable

        self.downloading = None
        self.btn.configure(state="normal")
        self._sync_quick()
        lang = ui_lang()
        if res == "installed" and vbcable.has_cable(vbcable.output_names()):
            self._status_text("")
            messagebox.showinfo(vbcable.title_text(lang), vbcable.howto_text(lang), parent=self.root)
            self.toggle("speak")
            return
        self.v_mode.set("listen")
        if res == "page":
            import webbrowser

            self._status_text("")  # #804 review: drop the "Downloading VB-CABLE..." line

            webbrowser.open(vbcable.VBCABLE_PAGE)
            return
        self._status_text(vbcable.fail_text(lang))
        messagebox.showwarning(vbcable.title_text(lang), vbcable.fail_text(lang), parent=self.root)

    def _pack_done(self, pack: str, rc: int):
        self.downloading = None
        self.btn.configure(state="normal")
        self._status_text(t("gui_dl_ok" if rc == 0 else "gui_dl_fail", model=pack))
        if rc != 0:
            self.panel.deiconify()
            self.panel.lift()
        self._sync_quick()
        if rc == 0:  # on without restarting the app
            self.toggle(getattr(self, "pack_retry", None))

    def _cpu_ram(self):
        """#780: RAM (GB) for the "too heavy" model marks on a PC without a CUDA device, else None."""
        try:
            from ..device import cuda_device_count

            return cpu_ram_gb(cuda_device_count() > 0)
        except Exception:  # noqa: BLE001 - a failed probe must not break the window
            return None

    def _download_done(self, model: str, rc: int):
        self.downloading = None
        self.btn.configure(state="disabled" if self.load_t0 is not None else "normal")  # #332: not while loading
        self._quality_changed()
        labels = model_labels(ram_gb=self._cpu_ram())
        self.combos["model"].configure(values=labels, width=combo_width(labels))
        if model_from_label(self.v_model.get()) == model:
            self.v_model.set(label_for_model(model, ram_gb=self._cpu_ram()))
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
        s.update(lang=lang_from_label(self.v_lang.get()), target=self.v_target.get(), engine=engine_from_label(self.v_engine.get()), quality=self.v_quality.get(),
                 model=model_from_label(self.v_model.get()), preset=self.v_preset.get(), glossary=self.v_gloss.get().strip(), save=self.v_save.get(),
                 save_path=self.v_save_path.get().strip(), show_src=self.var_show_src.get(),
                 live_draft=self.v_live.get(), source=self.v_source.get(), floating_bar=self.v_bar.get(),
                 voice=self.v_voice.get(), send_mode=self.v_send_mode.get(),
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
        if mode == "speak" and self._offer_cable():  # #804: no virtual mic -> offer the VB-CABLE install first
            return
        if self._offer_pack(run, mode):  # #673: a missing language pack is offered here; Start resumes after it
            return
        self.quick_mode, self.run_source = mode, run.get("source")
        self.speak_hint = speak_route(run, meeting_cable()) if mode == "speak" else None  # #710
        self.state = SubtitleState(HISTORY_LINES)
        self.set_paused(False)
        self.stop_ev = threading.Event()
        self.load_t0 = time.monotonic()  # #332: Stop stays disabled ("Loading... N s") until the models are ready
        self.load_shown = 0
        self.btn.configure(text=t("gui_loading", s=0), state="disabled")
        self.set_status("gui_starting")
        self.set_dot("loading")
        notice_key, show_panel = speak_start_view(self.speak_hint)  # #710: the Speak lines live in the Adjust panel
        self.v_notice.set(t(notice_key) if notice_key else "")
        if show_panel:
            self._show_panel()
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

            a = parse_args(settings_to_argv(s, outputs=voice_outputs() if s.get("voice") else None,
                                            notice=lambda m: q.put(("notice", m))))

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
                        self.set_dot("on")
                        ready = "gui_ready_speak" if self.run_source == "mic" else "gui_ready_listen"
                        if self.speak_hint == SPEAK_HINT_CABLE:  # #710: voice goes into CABLE Input
                            ready = self.speak_hint
                        self._status_text(f"{t(ready)} ({ev[1]})")  # #294: visible "start playback now"
                        self._sync_quick()
                elif kind == "notice":
                    self.v_notice.set(ev[1])
                    if self.speak_hint == SPEAK_HINT_CABLE:  # #710: e.g. the meeting voice turned itself off
                        self._show_panel()
                elif kind == "pack_line":
                    self._status_text(ev[1])
                elif kind == "cable_done":
                    self._cable_done(ev[1])
                elif kind == "pack_done":
                    self._pack_done(ev[1], ev[2])
                elif kind == "dl_line":
                    self._status_text(ev[1])
                elif kind == "doctor":
                    self._show_setup_check(ev[1], ev[2])
                elif kind == "dl_done":
                    self._download_done(ev[1], ev[2])
                elif kind.startswith("upd_"):
                    self._update_event(ev)
                elif kind == "error":
                    self._status_text(t("gui_error", msg=ev[1]))
                    self.errored = True
                    self.set_dot("error")
                    self.panel.deiconify()  # the error text is in the Adjust panel; a red dot alone says nothing
                    self.panel.lift()
                elif kind == "stopped":
                    self.load_t0 = None
                    self.btn.configure(text=t("gui_start"), state="normal")
                    if not self.errored:
                        self.set_status("gui_stopped")
                        self.set_dot("idle")
                    self.listening = False
                    self.quick_mode = None
                    self._sync_quick()
                    if self.tour.visible:  # #314: the overlay still points at the bar
                        self.overlay.render(self.state)
                    else:
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
            _diag.swallowed(__name__)
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.after(POLL_MS, self.poll)
        self.root.after(UPDATE_CHECK_DELAY_MS, self._auto_update_check)  # #698: after the window is up
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
