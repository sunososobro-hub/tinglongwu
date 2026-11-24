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
from .icon import apply_icon, icon_path, set_app_id
from ..langs import from_label as lang_from_label, label as lang_label, labels as lang_labels
from ..i18n import SILENT_CLEAR
from .core import scene_of as scene_mode_name
from . import core
from .core import (SpeakerColors, SPK_AVATAR_FG, speaker_initial, speaker_text, SCENES, WATCH_MODE, scene_mode, scene_runs, mic_toggle_visible, toggled_source, strip_silent_hint, GUI_SEND_MODES, BOTH_MODE, SPEAK_MARK, SpeakQueueSink, start_button, cpu_ram_gb, choice_label, choice_labels, choice_value, SPEAK_HINT_CABLE, meeting_cable, quick_settings, SameLanguageError, SEND_MODES, send_mode_notice_key, restart_needed, send_gate_line, speak_route, speak_start_view, tour_on_start, USAGE_CHOICES, usage_answer, usage_should_ask, tour_text, ALPHA_STEP, FONT_MAX, FONT_MIN, ROWS_MAX, ROWS_MIN, HISTORY_LINES, PRESETS, QUALITIES, SOURCES, TARGETS, QueueDraftRenderer,
                   QueueSink, SubtitleState, engine_from_label, engine_labels, clamp_font, clamp_rows, follow_newest, load_settings,
                   combo_width, doctor_lang, is_downloaded, label_for_model, model_from_label, model_labels, rows_after_drag, run_download, run_setup_check, missing_start_model, StartDownload, quality_hint, quality_speech_model,
                   save_settings, settings_to_argv, topics_on, topics_model_path, voice_outputs, listen_outputs, mic_inputs, clamp_alpha, run_pack_download, gui_error_text, load_status_text)
from . import autoupdate, pack_on_demand
from .topics_panel import notes_float_on
from .. import __version__, userdirs

try:  # optional theme; the GUI works without it
    import sv_ttk
except ImportError:  # pragma: no cover
    sv_ttk = None

POLL_MS = 50
UPDATE_CHECK_DELAY_MS = 5000
def set_titlebar_dark(win, dark: bool) -> bool:
    """#1662: dark (or light) OS title bar via DWMWA_USE_IMMERSIVE_DARK_MODE on the Toplevel's real HWND; Windows only."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # 19 on older Win10 builds
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                return True
    except Exception:  # noqa: BLE001
        _diag.swallowed(__name__)
    return False


def _mtime(p):
    """#1525: identity of the installed GPU pack folder (a replacement gets a new mtime); None when absent."""
    try:
        return p.stat().st_mtime_ns if p is not None else None
    except OSError:
        return None


UPDATE_LINE_WRAP_PX = 200  # #455: text + two buttons stay inside the 420 px main window
# #886: Advanced settings sit in two label + control columns (grid columns 0-2 and 4-6, 3 is a gap), so the panel
# with Advanced open fits a 1366x768 screen; long labels wrap instead of widening the panel
SETTING_COLS = 2
SPAN = 7  # grid columns a full-width row spans
LABEL_WRAP_PX = 190
ADV_PAD_X = 20  # #939: left + right padding of the Advanced card (its settings grid gets the rest of the width)
SHORT_SCREEN_PX = 900
UPDATE_ROW = 14  # #756: grid row of the Check setup / Report / update controls; below the last setting slot (13, #894)
PAIR_RESTART_MS = 800  # #1020: quiet time after a home-card language change before the running session restarts
HOME_PAIR_KEYS = ("lang", "target")  # #1020: restart by themselves; the #983 notice is for the other settings
UPDATE_QUIT_DELAY_MS = 1500  # #698: let the "Updating..." line paint before the window closes
UNINSTALL_URI = "ms-settings:appsfeatures"  # #738: File > Uninstall opens Windows "Installed apps"
FONT_MENU_SIZES = tuple(range(FONT_MIN, FONT_MAX + 1, 4))  # #738: Settings > Font size choices (12, 16, ... 72)
UI_LANGS = {"繁體中文": "zh-TW", "English": "en", "Русский": "ru", "日本語": "ja"}
STATUS_WRAP_MIN_PX = 200  # #1241: status / notice wrap at the panel width, never narrower than this
BAR_WIDE_PX = 1200  # #1241: from this bar width the 情境 dropdown and the toggles share one row
FONT_INLINE_PX = 500  # chat toolbar: font controls share the row from this width
BAR_ONE_ROW_PX = 1200  # the whole bottom bar (情境, toggles, Adjust / Freeze / Start) on one row from this width
MAIN_W, MAIN_H, MAIN_MIN_W, MAIN_MIN_H = 460, 640, 380, 480  # #1028: the chat-style main window
# chat bubbles (#1028): other side on the left, me on the right; (bubble background, text) per theme
# #1644: "latest" = the newest finished line from the other side (brighter, so the eye lands on it first)
# #1662 Linear-style palette: window / transcript backgrounds differ slightly, 1 px separators, muted secondary text,
# bright primary text and ONE accent (the existing blue); warn / err are the only other colours
PAL = {"dark": {"win": "#14161c", "bg": "#0e1015", "sep": "#262a34", "fg": "#e6e8ee", "muted": "#8a90a0",
                "accent": "#3f5fe0", "warn": "#d29b3c", "err": "#e5534b"},
       "light": {"win": "#f6f7fa", "bg": "#ffffff", "sep": "#dfe2ea", "fg": "#1d2447", "muted": "#5b6390",
                 "accent": "#3f5fe0", "warn": "#b35a00", "err": "#c0392b"}}
BUBBLE = {"dark": {"bg": "#0e1015", "other": ("#1a1d27", "#e6e8ee"), "me": ("#3f5fe0", "#ffffff"),
                   "latest": ("#2a3560", "#ffffff"), "sub": "#8a90a0", "head": "#8a90a0"},
          "light": {"bg": "#ffffff", "other": ("#e6e9f5", "#1d2447"), "me": ("#cfe0ff", "#10224d"),
                    "latest": ("#cdd6ff", "#10224d"), "sub": "#5b6390", "head": "#5b6390"}}
# #1644: notes (重點整理) window per theme: time range muted, bullets in the notes accent (violet)
NOTES = {"dark": {"bg": "#0e1015", "fg": "#e6e8ee", "title": "#ffffff", "when": "#8a90a0", "mark": "#b79cff"},
         "light": {"bg": "#ffffff", "fg": "#1d2447", "title": "#10224d", "when": "#5b6390", "mark": "#6b4fd8"}}
FG_FINAL, FG_DRAFT, FG_SRC, BG = "#ffffff", "#8a8a8a", "#b8c4d6", "#101010"
FONT_FAMILY = "Microsoft JhengHei UI" if sys.platform == "win32" else "TkDefaultFont"
# settings-panel sidebar: dragon logo on a lapis-blue -> lavender gradient (mascot palette), nav drawn on the canvas
SIDE_W = 168
SIDE_LOGO_PX = 84
SIDE = {"dark": {"top": "#1d2447", "bottom": "#2c2350", "fg": "#eef0ff", "sub": "#a9b1d6", "pill": "#4a63d8",
                 "wave": "#3a3f72"},
        "light": {"top": "#e9eeff", "bottom": "#e3dcfa", "fg": "#1d2447", "sub": "#5b6390", "pill": "#4a63d8",
                  "wave": "#cfd3f3"}}
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

    def __init__(self, master: tk.Tk, app: App):
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
        if hasattr(app, "report_wrong"):  # #677: GUI only
            self.menu.add_command(label=t("gui_menu_wrong"), command=app.report_wrong)
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
        fam = self.app.settings.get("font_family") or FONT_FAMILY  # user-picked system font, else the default
        self.font = tkfont.Font(family=fam, size=n, weight="bold")
        for tag in ("final", "draft"):
            self.text.tag_configure(tag, font=self.font)
        self.text.tag_configure("src", font=(fam, max(9, n * 2 // 3)))
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
        pfx = getattr(self.app, "speaker_prefix", None)  # the CLI Bar host has none: no labels, as before
        for i, (src, text, is_draft) in enumerate(rows):
            if show_src and src:
                t.insert("end", src + "\n", "src")
            p = pfx(i) if pfx is not None and state.history() else None
            if p:  # 0.3.8: colored "● Speaker 2: " in front of the same line (no extra row, no extra height)
                tag = "spkc" + p[1]
                t.tag_configure(tag, foreground=p[1], font=self.font)
                t.insert("end", p[0], tag)
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

    def __init__(self, app: App):
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


_NVML = {}


def _nvml_gpu_percent():
    """GPU 0 busy % via the driver's own nvml library (ctypes, no subprocess, no extra package); None when absent."""
    import ctypes
    if "h" not in _NVML:
        _NVML["h"] = None
        try:
            lib = ctypes.CDLL("nvml.dll" if os.name == "nt" else "libnvidia-ml.so.1")
            if lib.nvmlInit_v2() == 0:
                dev = ctypes.c_void_p()
                if lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev)) == 0:
                    _NVML["h"] = (lib, dev)
        except Exception:  # noqa: BLE001
            pass
    if not _NVML["h"]:
        return None
    lib, dev = _NVML["h"]

    class _Util(ctypes.Structure):
        _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

    u = _Util()
    if lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u)) != 0:
        return None
    return int(u.gpu)


DOCK_W = 320  # width the window grows by when the meeting-notes pane docks on the right


class _DockPane(ttk.Frame):
    """The meeting-notes pane, docked to the right of the subtitle log (was a floating Toplevel). It keeps the
    withdraw/deiconify surface of the old window so callers and tests stay the same."""

    def __init__(self, app, holder, before):
        super().__init__(holder, width=DOCK_W)
        self._app, self._before, self._grew = app, before, 0
        grip = tk.Frame(self, width=3, cursor="sb_h_double_arrow", bg="#555555")  # drag the left edge to resize
        grip.pack(side="left", fill="y")
        grip.bind("<B1-Motion>", self._drag)
        grip.bind("<Enter>", lambda e: grip.configure(bg="#2e86de"))  # a thin line that lights up under the mouse
        grip.bind("<Leave>", lambda e: grip.configure(bg="#555555"))
        self.bind("<Configure>", self._rewrap, add="+")

    def _drag(self, e):
        w = max(220, min(self.winfo_width() - (e.x_root - self.winfo_rootx()), self.master.winfo_width() - 160))
        self.configure(width=w)

    def _rewrap(self, e):
        for name in ("topics_lang", "topics_hint"):
            lb = getattr(self._app, name, None)
            if lb is not None:
                try:
                    lb.configure(wraplength=max(120, e.width - 40))
                except Exception:  # noqa: BLE001
                    pass

    def deiconify(self):
        if not self.winfo_manager():
            self.pack(side="right", fill="y", padx=(6, 0), before=self._before)
            self.pack_propagate(False)
            root = self._app.root
            try:
                root.update_idletasks()
                if root.winfo_width() < 800 and root.winfo_screenwidth() > root.winfo_width() + DOCK_W + 40:
                    self._grew = DOCK_W
                    root.geometry(f"{root.winfo_width() + DOCK_W}x{root.winfo_height()}")
            except Exception:  # noqa: BLE001
                self._grew = 0

    def withdraw(self):
        self.pack_forget()
        root = self._app.root
        if self._grew:
            try:
                root.geometry(f"{max(MAIN_MIN_W, root.winfo_width() - self._grew)}x{root.winfo_height()}")
            except Exception:  # noqa: BLE001
                pass
            self._grew = 0


class _FloatPane(tk.Toplevel):
    """#1705: the meeting-notes pane popped out into its own always-on-top, draggable, resizable window (like the
    subtitle bar). Same withdraw/deiconify surface as _DockPane; the content is rebuilt from the TopicPanel model,
    so there is no second data path. Closing the window docks the notes back into the side panel."""

    def __init__(self, app):
        from .topics_panel import NOTES_MIN_H, NOTES_MIN_W, notes_geometry

        super().__init__(app.root)
        self._app = app
        self.withdraw()
        self.title(t("gui_topics_title"))
        self.attributes("-topmost", True)
        self.resizable(True, True)
        self.minsize(NOTES_MIN_W, NOTES_MIN_H)
        self.geometry(notes_geometry(app.settings, self.winfo_screenwidth(), self.winfo_screenheight()))
        self.protocol("WM_DELETE_WINDOW", app._notes_dock)
        self.bind("<Configure>", self._configure, add="+")
        set_titlebar_dark(self, app.settings.get("theme") != "light")

    def _configure(self, e):
        if e.widget is not self:
            return
        from .topics_panel import remember_notes_geometry

        remember_notes_geometry(self._app.settings, self.winfo_width(), self.winfo_height(),
                                self.winfo_x(), self.winfo_y())
        for name in ("topics_lang", "topics_hint"):
            lb = getattr(self._app, name, None)
            if lb is not None:
                try:
                    lb.configure(wraplength=max(120, e.width - 40))
                except Exception:  # noqa: BLE001
                    pass

    def winfo_manager(self):  # callers ask "is the pane shown?" (_topics_toggle)
        return "wm" if self.state() == "normal" else ""


def _system_cpu_percent(app):
    """Busy share of all CPUs since the previous call, in percent, or None (not Windows / first call / error)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
        return None

    def n(ft):
        return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

    cur = (n(idle), n(kern) + n(user))  # kernel time already includes idle time
    prev, app._cpu_prev = app._cpu_prev, cur
    if prev is None:
        return None
    d_total = cur[1] - prev[1]
    d_idle = cur[0] - prev[0]
    if d_total <= 0:
        return None
    return max(0, min(100, round(100 * (d_total - d_idle) / d_total)))


class App:
    def __init__(self):
        self.settings = load_settings()
        core.set_tech_labels(setting=self.settings["tech_labels"])  # #1806
        self.q: queue.Queue = queue.Queue()
        self.state = SubtitleState(HISTORY_LINES)
        self.listening = False  # a session is running (Start pressed, worker not yet stopped)
        self.spk_clusterer = None  # per-session SpeakerClusterer (reset on Start only)
        self._slow_after = None  # Tk after() id of the 10 s slow-load line
        self._slow_shown = False
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
        self.root.resizable(True, True)  # #939: a normal desktop window
        self.root.geometry(f"{MAIN_W}x{MAIN_H}")
        self.root.minsize(MAIN_MIN_W, MAIN_MIN_H)
        # #455 kept the small control bar -topmost so it stayed reachable over a meeting window. #1028 makes the main
        # window a normal chat window (#939: resizable, 460x640): always-on-top would cover the meeting, and the
        # floating subtitle bar (still -topmost) is the thing that must stay on top. So no -topmost here on purpose.
        self.mic_ev = threading.Event()  # #1028: mic toggle; set = the speak leg's capture is fed silence (MuteSource)
        from .topics_panel import TopicPanel

        # #611: finished topics arrive on the summarizer thread; poll() redraws the topic notes window
        self.topic_panel = TopicPanel(on_change=lambda: self.q.put(("topics",)))
        self.topics_win = None
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self._tr: list[tuple[object, str, str]] = []  # (widget, option, i18n key) re-labelled on language switch
        self.status_key: str | None = "gui_ready"
        from ..speakers import SpeakerBook

        self.spk_colors = SpeakerColors()  # 0.3.8: avatar colors per speaker, first-seen order
        self.spk = None  # 0.3.8: async speaker-ID labeler of this session (None = unavailable)
        self._spk_tags_bound: set[str] = set()  # speaker-label tags already bound on self.log_text
        self.spk_book = SpeakerBook()  # session-only names
        self.quick_mode: str | None = None  # #294: first-screen button that started this session
        self.run_source = "system"
        self.speak_hint: str | None = None  # #710: speak_route() result for this session
        self._make_main()
        self._build_controls()
        self._build_quick()
        self._build_bar()
        self._build_update_line()
        self._build_gpu_offer()  # #1525: one-click GPU speed-up pack offer (NVIDIA only)
        self.v_tech = tk.BooleanVar(master=self.root, value=self.settings["tech_labels"])  # #1806: plain wording by default
        self.build_menubar()
        self.overlay = Overlay(self.root, self)
        self._sync_bar()  # #1042: the bar is not on screen before Start
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
        widget.configure(**{option: core.ui_text(key)})  # #1807: plain / tech wording
        self._tr.append((widget, option, key))
        return widget

    def set_status(self, key: str):
        self.status_key = key
        self.v_status.set(t(key))

    def _slow_clear(self):
        """Cancel the slow-load timer and forget the line (ready, Stop, error, new Start)."""
        if self._slow_after is not None:
            try:
                self.root.after_cancel(self._slow_after)
            except Exception:  # noqa: BLE001
                pass
        self._slow_after, self._slow_shown = None, False

    def _slow_load_due(self):
        """10 s after Start and still loading: add the friendly line under the loading status."""
        self._slow_after = None
        if self.load_t0 is None or self.errored:
            return
        from .core import slow_load_line

        line = slow_load_line(time.monotonic() - self.load_t0)
        if line:
            self._slow_shown = True
            self._status_text(f"{self.v_status.get()}\n{line}")

    def _status_text(self, text: str):
        self.status_key = None
        self.v_status.set(text)

    def _dedupe_notice(self):
        """#1241: the CPU-mode notice appears once (the status line keeps it, with the MT ms)."""
        from .core import drop_cpu_mode_notice

        self.v_notice.set(drop_cpu_mode_notice(self.v_notice.get(), self.v_status.get()))

    def _wrap_status(self, width: int):
        """#1241: status / notice lines wrap at the panel width, not a fixed 420 px (a wide window was half empty)."""
        wl = max(STATUS_WRAP_MIN_PX, int(width) - 8)
        for lbl in (self.lbl_status, self.lbl_notice):
            if int(str(lbl.cget("wraplength") or 0)) != wl:
                lbl.configure(wraplength=wl)

    @property
    def pal(self) -> dict:
        return PAL["light" if self.settings.get("theme") == "light" else "dark"]

    def _tok(self, w, token: str):
        """#1662: a label whose colour is a palette token (re-tinted on every theme change)."""
        self._tok_widgets = [(x, k) for x, k in getattr(self, "_tok_widgets", []) if x.winfo_exists()]
        self._tok_widgets.append((w, token))
        w.configure(foreground=self.pal[token])
        return w

    def _style_menu(self, m):
        pal = self.pal
        try:
            m.configure(bg=pal["win"], fg=pal["fg"], activebackground=pal["accent"], activeforeground="#ffffff",
                        selectcolor=pal["fg"], bd=0, relief="flat", activeborderwidth=0)
            end = m.index("end")
            for i in range(0 if end is None else end + 1):
                if m.type(i) == "cascade":
                    self._style_menu(m.nametowidget(m.entrycget(i, "menu")))
        except tk.TclError:
            _diag.swallowed(__name__)

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
        st.configure("Adv.Toolbutton", anchor="w", padding=(8, 2))
        pal = self.pal  # #1662: one palette; ttk frames / labels take the window colours, secondary text is muted
        for style in ("TFrame", "TLabel", "TCheckbutton", "TRadiobutton", "Toolbutton"):
            st.configure(style, background=pal["win"])
        st.configure("TLabel", foreground=pal["fg"])
        st.configure("Menu.TMenubutton", padding=(8, 0), background=pal["win"], foreground=pal["fg"])
        st.configure("Card.TFrame", background=pal["win"])
        st.configure("Start.Accent.TButton", font=(FONT_FAMILY, 13, "bold"), padding=(14, 8))  # #1028: the big Start
        self._retint()
        if sv_ttk is not None and not getattr(self, "_retint_bound", False):
            # #1644: sv_ttk recolours every tk widget on <<ThemeChanged>> (tk_setPalette), after this method has
            # returned, and that wiped the chat / notes background; paint ours again once it is done
            self._retint_bound = True
            self.root.bind("<<ThemeChanged>>",
                           lambda e: self.root.after_idle(self._retint) if e.widget is self.root else None, add="+")
        if getattr(self, "side", None) is not None:
            self._draw_sidebar()

    def _retint(self):
        """Our own colours on the plain tk.Text widgets (chat log, notes window) for the current theme."""
        pal = self.pal
        for w, k in list(getattr(self, "_tok_widgets", [])):
            try:
                w.configure(foreground=pal[k])
            except tk.TclError:
                pass
        try:
            self.root.configure(bg=pal["win"])
            self.panel.configure(bg=pal["win"])
        except (tk.TclError, AttributeError):
            pass
        dark = self.settings.get("theme") != "light"
        for name in ("root", "panel", "topics_win"):
            w = getattr(self, name, None)
            try:
                if w is not None and w.winfo_exists():
                    set_titlebar_dark(w, dark)
            except tk.TclError:
                pass
        for name in ("menubar", "panel_menubar"):
            if getattr(self, name, None) is not None:
                self._style_menu(getattr(self, name))
        for name in ("menu_sep", "panel_menubar_sep"):
            w = getattr(self, name, None)
            if w is not None and w.winfo_exists():
                w.configure(bg=pal["sep"])
        if getattr(self, "log_text", None) is not None:
            self._chat_tags()
        if getattr(self, "topics_win", None) is not None and self.topics_win.winfo_exists():
            self._notes_tags()

    # -- settings-panel sidebar (logo + nav), drawn on one canvas so it adds no interactive widgets ------------------
    def _build_sidebar(self, p):
        self.side = c = tk.Canvas(p, width=SIDE_W, highlightthickness=0, bd=0)
        c.pack(side="left", fill="y")
        self.side_logo = None
        try:
            img = tk.PhotoImage(master=p, file=str(icon_path("tinglongwu.png")))
            k = max(1, img.width() // SIDE_LOGO_PX)
            self.side_logo = img.subsample(k, k) if k > 1 else img
        except Exception:  # noqa: BLE001 - a missing logo must never stop the GUI
            _diag.swallowed(__name__)
        c.bind("<Configure>", lambda _e: self._draw_sidebar())
        self.side_nav = (("gui_menu_settings", "⚙", None), ("gui_menu_about", "ⓘ", self.show_about),
                         ("gui_menu_help", "?", self.show_tour))

    def _draw_sidebar(self):
        c = self.side
        pal = SIDE["light" if self.settings.get("theme") == "light" else "dark"]
        c.delete("all")
        h = max(c.winfo_height(), 400)
        top, bot = (tuple(int(col[i:i + 2], 16) for i in (1, 3, 5)) for col in (pal["top"], pal["bottom"]))
        steps = 48
        for i in range(steps):
            mix = [round(a + (b - a) * i / (steps - 1)) for a, b in zip(top, bot)]
            y0 = h * i // steps
            c.create_rectangle(0, y0, SIDE_W, h * (i + 1) // steps + 1, outline="", fill="#%02x%02x%02x" % tuple(mix))
        # soft sound-wave arcs at the bottom (listening), instead of a picture background
        for r in (70, 110, 150, 190):
            c.create_arc(SIDE_W // 2 - r, h - r // 3, SIDE_W // 2 + r, h + r * 5 // 3, start=40, extent=100,
                         style="arc", outline=pal["wave"], width=2)
        y = 22
        if self.side_logo is not None:
            c.create_image(SIDE_W // 2, y, image=self.side_logo, anchor="n")
            y += self.side_logo.height() + 6
        c.create_text(SIDE_W // 2, y, text="聽攏有", fill=pal["fg"], font=(FONT_FAMILY, 15, "bold"), anchor="n")
        c.create_text(SIDE_W // 2, y + 30, text="tinglongwu", fill=pal["sub"], font=(FONT_FAMILY, 10), anchor="n")
        y += 74
        for i, (key, glyph, cmd) in enumerate(self.side_nav):
            tag = f"nav{i}"
            active = cmd is None
            if active:
                self._round_rect(12, y - 4, SIDE_W - 12, y + 32, 10, fill=pal["pill"], outline="", tags=tag)
            fg = "#ffffff" if active else pal["fg"]
            c.create_text(30, y + 14, text=glyph, fill=fg, font=(FONT_FAMILY, 12, "bold"), tags=tag)
            c.create_text(52, y + 14, text=t(key), fill=fg, font=(FONT_FAMILY, 11), anchor="w", tags=tag)
            if cmd is not None:
                c.tag_bind(tag, "<Button-1>", lambda _e, f=cmd: f())
                c.tag_bind(tag, "<Enter>", lambda _e: c.configure(cursor="hand2"))
                c.tag_bind(tag, "<Leave>", lambda _e: c.configure(cursor=""))
            y += 44

    def _round_rect(self, x0, y0, x1, y1, r, **kw):
        pts = (x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1, x0, y1,
               x0, y1 - r, x0, y0 + r, x0, y0)
        return self.side.create_polygon(pts, smooth=True, **kw)

    def _toggle_theme(self):
        self.apply_theme("dark" if self.v_dark.get() else "light")

    def switch_lang(self, _e=None):
        set_ui_lang(UI_LANGS[self.v_ui_lang.get()])
        self.relabel()

    def relabel(self):
        for w, opt, key in self._tr:
            w.configure(**{opt: core.ui_text(key)})
        if self.load_t0 is not None:
            self.btn.configure(text=t("gui_loading", s=self.load_shown))
        else:
            self.btn.configure(text=t("gui_stop" if self.worker is not None and self.worker.is_alive() else "gui_start"))
        self.btn_pause.configure(text=t("gui_resume" if self.state.paused else "gui_pause"))
        self._mic_label()
        self._adv_toggled()
        self._send_mode_changed()  # #834: the notice follows the UI language
        if self.status_key:
            self.v_status.set(t(self.status_key))
        self._sync_quick()
        if getattr(self, "scene_combo", None) is not None:  # the dropdown shows names in the new UI language
            self.scene_combo.configure(values=self._scene_labels())
            self.scene_combo.set(t("gui_scene_" + self.v_scene.get()))
            self._source_sync()  # #1544
        self.combos["lang"].configure(values=lang_labels())
        self._relabel_combos()
        self.home_lang.configure(values=lang_labels())
        self.v_lang.set(lang_label(lang_from_label(self.v_lang.get())))
        self._pair_changed()
        self.overlay.build_menu()
        self.log_menu.entryconfigure(0, label=t("gui_menu_wrong_line"))
        self.log_menu.entryconfigure(1, label=t("gui_log_copy"))
        self.build_menubar()
        self._upd_relabel()
        self._draw_sidebar()
        self.tour.relabel()
        self.render_log()
        if self.overlay.visible:
            self.overlay.render(self.state)

    def _build_send_mode(self, parent):
        """#834: speak send mode (default auto = send straight away, #1052), shown in plain language (#1028); countdown / auto show the
        quality notice. The "restart to apply" line (#983) is packed at the top of the settings page."""
        box = ttk.Frame(parent)
        self.v_send_mode = tk.StringVar(value=self.settings["send_mode"])
        for mode, key in zip(GUI_SEND_MODES, ("gui_send_auto", "gui_send_confirm")):
            self._tx(ttk.Radiobutton(box, variable=self.v_send_mode, value=mode), key).pack(anchor="w")
        self.lbl_send_notice = ttk.Label(box, wraplength=300, justify="left")
        self.lbl_send_notice.pack(anchor="w")
        self.v_send_mode.trace_add("write", lambda *_: self._send_mode_changed())
        self._send_mode_changed()
        self.run_snapshot = None  # #983: the start-time values of the running session
        self.lbl_restart = ttk.Label(self.body, wraplength=300, justify="left", foreground="#b45309")
        self._tok(self.lbl_restart, "warn")
        self.btn_restart = ttk.Button(self.body, command=self._restart_now)
        for name in ("v_lang", "v_target", "v_engine", "v_quality", "v_model", "v_preset", "v_gloss", "v_source",
                     "v_voice", "v_voice_dev", "v_gpu_use"):
            if hasattr(self, name):
                getattr(self, name).trace_add("write", lambda *_: self._restart_check())
        self.run_pair = None  # #1020: (my language, other side's language) the running session started with
        self._pair_after = None
        for name in ("v_lang", "v_target"):
            getattr(self, name).trace_add("write", lambda *_: self._pair_restart_soon())
        return box

    def _build_send_gate(self, f):
        """#833: the held speak line with [Send] [Stop] in the status box `f`. Its widgets are made the first time a
        line is held (a fresh first screen keeps only its #737 controls). Space = stop, Esc = stop."""
        self.gate = None  # reverse.sendgate.SendGate of the running speak session
        self.gate_parent, self.gate_frm = f, None
        self.v_gate = tk.StringVar(value="")
        for w in (self.root, self.panel):  # keys only act while a line is held, so typing elsewhere is untouched
            w.bind("<space>", lambda _e: self._gate_key("stop"), add="+")
            w.bind("<Escape>", lambda _e: self._gate_key("stop"), add="+")

    def _gate_widgets(self):
        if self.gate_frm is None:
            self.gate_frm = ttk.Frame(self.gate_parent)
            self.lbl_gate = ttk.Label(self.gate_frm, textvariable=self.v_gate, wraplength=300, justify="left")
            self.lbl_gate.grid(row=0, column=0, columnspan=2, sticky="w")
            self.btn_gate_send = ttk.Button(self.gate_frm, text=t("gui_send_now"),
                                            command=lambda: self._gate_do("confirm"))
            self.btn_gate_send.grid(row=1, column=0, sticky="we", padx=(0, 2))
            self.btn_gate_stop = ttk.Button(self.gate_frm, text=t("gui_send_stop"),
                                            command=lambda: self._gate_do("stop"))
            self.btn_gate_stop.grid(row=1, column=1, sticky="we")
        return self.gate_frm

    def _gate_key(self, action):
        if self.gate is not None and self.gate.view().state != "idle" and not isinstance(
                self.root.focus_get(), (tk.Entry, ttk.Entry, ttk.Combobox, ttk.Button)):
            self._gate_do(action)
            return "break"

    def _gate_do(self, action):
        if self.gate is not None:
            getattr(self.gate, action)()

    def _gate_tick(self):
        """GUI thread, every 100 ms: advances the countdown and redraws the held line."""
        g = self.gate
        if g is None:
            return
        g.tick()
        v = g.view()
        self.v_gate.set(send_gate_line(v))
        if v.state == "idle":
            if self.gate_frm is not None:
                self.gate_frm.pack_forget()
        else:
            frm = self._gate_widgets()
            if not frm.winfo_manager():  # a line was just held: show it (and the panel, once)
                frm.pack(fill="x", pady=2)
                if self.speak_hint == SPEAK_HINT_CABLE:
                    self._show_panel()
            self.btn_gate_send.configure(state="normal" if v.state == "asking" else "disabled")
            self.lbl_gate.configure(foreground=self.pal["muted"] if v.state == "rejected" else self.pal["fg"])  # #968: not sent

    def _start_values(self) -> dict:
        """#983: current raw values of the start-time settings (same getters at start and later, so they compare)."""
        names = dict(lang="v_lang", target="v_target", engine="v_engine", quality="v_quality", model="v_model",
                     preset="v_preset", glossary="v_gloss", source="v_source", voice="v_voice",
                     voice_device="v_voice_dev", send_mode="v_send_mode", gpu_use="v_gpu_use")
        return {k: getattr(self, v).get() for k, v in names.items() if hasattr(self, v)}

    def _restart_check(self):
        """#983: show the "restart to apply" notice + button while running with a changed start-time setting."""
        running = self.worker is not None and self.worker.is_alive()
        changed = restart_needed(self.run_snapshot, self._start_values()) if running and self.run_snapshot else []
        notice = [k for k in changed if k not in HOME_PAIR_KEYS]  # #1020: the home-card pair restarts by itself
        if notice:
            self.lbl_restart.configure(text=t("gui_restart_to_apply"))
            self.btn_restart.configure(text=t("gui_restart_btn"))
            if not self.lbl_restart.winfo_manager():
                self.lbl_restart.pack(anchor="w", before=self.settings_title)
                self.btn_restart.pack(anchor="w", before=self.settings_title)
        else:
            self.lbl_restart.pack_forget()
            self.btn_restart.pack_forget()
        return changed

    def _home_pair(self) -> tuple:
        """#1020: the home card's language pair as codes (a UI-language switch relabels v_lang, same language)."""
        return lang_from_label(self.v_lang.get()), self.v_target.get()

    def _pair_restart_soon(self):
        """#1020: a language changed on the home card while running -> restart after PAIR_RESTART_MS of quiet."""
        if self._pair_after is not None:
            self.root.after_cancel(self._pair_after)
        self._pair_after = self.root.after(PAIR_RESTART_MS, self._pair_restart_fire)

    def _pair_restart_fire(self):
        self._pair_after = None
        if self.worker is None or not self.worker.is_alive() or self.run_pair is None:
            return  # not running (a pending restart reads the new pair when it starts)
        if self._home_pair() == self.run_pair:
            return
        if self.load_t0 is not None or getattr(self, "_restart_mode", None) is not None or self.stop_ev.is_set():
            self._pair_after = self.root.after(PAIR_RESTART_MS, self._pair_restart_fire)  # loading / stopping
            return
        self._restart_now()

    def _restart_now(self):
        """#983: stop, then start the same mode again once the worker has ended (new values are read by collect)."""
        mode = self.quick_mode
        if self.worker is not None and self.worker.is_alive():
            self.toggle()  # stop
            self._restart_mode = mode
            self._restart_poll()

    def _restart_poll(self):
        if self.worker is not None and self.worker.is_alive():
            self.root.after(300, self._restart_poll)
            return
        mode, self._restart_mode = getattr(self, "_restart_mode", None), None
        self.toggle(mode)

    def _send_mode_changed(self):
        mode = self.v_send_mode.get()
        if getattr(self, "lbl_restart", None) is not None:
            self._restart_check()
        if mode in SEND_MODES:
            self.settings["send_mode"] = mode
        key = send_mode_notice_key(mode)
        self.lbl_send_notice.configure(text=t(key) if key else "")
        if key:  # #886: an empty notice line takes no height (the panel must fit a 768 px screen)
            self.lbl_send_notice.pack(anchor="w")
        else:
            self.lbl_send_notice.pack_forget()

    @staticmethod
    def _cell(slot: int, cols: int = SETTING_COLS) -> tuple[int, int]:
        """#886: (grid row, first grid column) of setting `slot`; slots fill `cols` columns left to right."""
        return slot // cols, (slot % cols) * 4

    def _put(self, w, slot, dcol=0, **kw):
        """#939: grid `w` in setting `slot` (column offset `dcol` inside the slot) and remember it for re-gridding."""
        row, col = self._cell(slot, getattr(self, "setting_cols", SETTING_COLS))
        w.grid(row=row, column=col + dcol, **kw)
        self.slot_widgets.setdefault(slot, []).append((w, dcol))
        return w

    def _set_setting_cols(self, cols: int) -> bool:
        """#939 (mockup A): two settings per row at the default width, one per row when the window is narrower.
        Hidden settings (grid_remove) stay hidden. Returns True when the layout changed."""
        if cols == self.setting_cols:
            return False
        self.setting_cols = cols
        for slot, items in self.slot_widgets.items():
            row, col = self._cell(slot, cols)
            for w, dcol in items:
                if w.winfo_manager() == "grid":
                    w.grid_configure(row=row, column=col + dcol)
        self.frm.columnconfigure(3, minsize=14 if cols == 2 else 0)
        self.frm.columnconfigure(5, weight=1 if cols == 2 else 0)
        return True

    def _adv_width_changed(self, width: int):
        """#939: re-grid when the Advanced card is narrower than the two-column layout needs (with hysteresis:
        the two-column width is measured while two columns are shown). While Advanced is collapsed the card's width
        comes from the other groups, not from the window, so it is ignored (it would lock the grid in one column)."""
        if getattr(self, "_measuring_panel", False):  # #1556: our own measuring re-grid delivers stale <Configure> events (macOS)
            return
        if not self.frm.winfo_ismapped():
            return
        if self.setting_cols == 2:
            self.two_col_w = max(self.two_col_w, self.frm.winfo_reqwidth())
        want = 2 if width >= self.two_col_w else 1
        if self._set_setting_cols(want):
            p = self.panel
            p.update_idletasks()
            mins = getattr(self, "panel_min", None)  # (one-column width, two-column height), set by _apply_panel_geometry
            if want == 1:
                if mins:  # #1097: the minimum height follows the active layout (one column is taller)
                    p.minsize(mins[0], max(mins[1], p.winfo_reqheight()))
                if p.winfo_height() < p.winfo_reqheight():  # one column is taller: never cut settings off
                    p.geometry(f"{p.winfo_width()}x{p.winfo_reqheight()}")
            elif mins:
                p.minsize(*mins)

    def _label(self, slot, key, cls=None, **kw):
        w = self._tx((cls or ttk.Label)(self.frm, **kw), key)
        if cls is None:
            w.configure(wraplength=LABEL_WRAP_PX, justify="left")
        return self._put(w, slot, sticky="w", pady=1)

    def _combo(self, slot, label, key, values, kind=None):
        """kind (#884): the box shows names in the UI language while the returned var keeps the raw stored value."""
        self._label(slot, label)
        var = tk.StringVar(value=self.settings[key])
        shown = var
        if kind:
            shown = tk.StringVar(value=choice_label(kind, var.get()))
            busy = []

            def sync(src, dst, conv):
                if busy:
                    return
                busy.append(1)
                try:
                    dst.set(conv(src.get()))
                finally:
                    busy.pop()

            var.trace_add("write", lambda *_: sync(var, shown, lambda v: choice_label(kind, v)))
            shown.trace_add("write", lambda *_: sync(shown, var, lambda v: choice_value(kind, v, values)))
            self.kind_combos[key] = (kind, values, var)
        self.combos[key] = cb = ttk.Combobox(self.frm, textvariable=shown, values=choice_labels(kind, values) if kind else values,
                                             state="readonly", width=18)
        self._put(cb, slot, 1, columnspan=2, sticky="we", pady=1, padx=(6, 0))
        return var

    def _relabel_combos(self):
        """#884: the UI language changed: show every named dropdown in the new language."""
        for key, (kind, values, var) in self.kind_combos.items():
            self.combos[key].configure(values=choice_labels(kind, values))
            var.set(var.get() + "")  # re-fires the write trace -> the shown name follows the language
        if getattr(self, "home_target", None) is not None:
            self.home_target.configure(values=self.combos["target"].cget("values"))
        # engine / model carry marks in the old language: rebuild them from the stored ids (settings value unchanged)
        eng = engine_from_label(self.v_engine.get())
        self.combos["engine"].configure(values=engine_labels())
        self.v_engine.set(next((l for l in engine_labels() if engine_from_label(l) == eng), eng))
        mod = model_from_label(self.v_model.get())
        labels = model_labels(ram_gb=self._cpu_ram())
        self.combos["model"].configure(values=labels)
        self.v_model.set(next((l for l in labels if model_from_label(l) == mod), mod))

    def set_advanced(self, on: bool):
        """#399: show / hide the engine dropdown; the choice is saved with the other GUI settings."""
        self.v_adv.set(bool(on))
        self._adv_toggled()

    def _adv_toggled(self):
        on = self.v_adv.get()
        self.settings["advanced_open"] = on
        if on:  # #737: the whole settings block (engine included) lives under Advanced
            self._set_setting_cols(2)  # #939: open at two columns; a narrow window re-wraps on the next <Configure>
            self.frm.pack(after=self.adv_hint, anchor="w", fill="x")
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
        self._asr_hint()

    def _gpu_allowed(self) -> bool:
        """#1531: False while "Use GPU" is Off (the variable does not exist yet while the panel is being built)."""
        v = getattr(self, "v_gpu_use", None)
        return v is None or v.get() != "off"

    def _speech_model(self, level: str) -> str | None:
        """#1385: the speech model a Start with this Quality uses (core.quality_speech_model); None when the
        hardware probe fails (the hint is left out, the Start behaves as before)."""
        try:
            if self.speech_hw is None:
                from ..device import resolve_device
                from ..hwprobe import probe

                self.speech_hw = (resolve_device("auto", warn=lambda m: None) == "cuda", probe())
            cuda, hw = self.speech_hw
            cuda = cuda and self._gpu_allowed()  # Use GPU: Off -> the CLI runs --device cpu, so the CPU tier decides
            return quality_speech_model(level, cuda, hw, manual_model=model_from_label(self.v_model.get()))
        except Exception:  # noqa: BLE001 - a failed probe must not break the settings panel
            return None

    def _asr_hint(self):
        """#1385: say which speech model the chosen Quality runs, in plain words, and its download size if missing."""
        lbl = getattr(self, "lbl_asr", None)
        if lbl is None:
            return
        m = self._speech_model(self.v_quality.get())
        lbl.configure(text="" if m is None else quality_hint(m, bool(self.speech_hw and self.speech_hw[0] and self._gpu_allowed()),
                                                                is_downloaded(m)))

    def _make_main(self):
        """#1028: the main window = language pickers on top, chat bubbles in the middle, status + one big Start and
        the mic / floating-subtitle toggles at the bottom. (Settings are the separate panel, opened with the gear.)"""
        r = self.root
        self.top_frame = ttk.Frame(r, padding=(12, 6, 12, 2))
        self.top_frame.pack(side="top", fill="x")
        self.bar = ttk.Frame(r, padding=(12, 2, 12, 6))
        self.bar.pack(side="bottom", fill="x")
        self.status_holder = ttk.Frame(r, padding=(12, 0, 12, 0))
        self.status_holder.pack(side="bottom", fill="x")
        self.chat_holder = ttk.Frame(r, padding=(8, 0, 8, 2))
        self.chat_holder.pack(side="top", fill="both", expand=True)

    def _build_bar(self):
        """#1028: bottom bar. Row 1: mic toggle, floating subtitles. Row 2: settings, status dot, freeze subtitles,
        the big Start / Stop at the right."""
        b = self.bar
        b.columnconfigure(1, weight=1)
        self.scene_row = scene_row = ttk.Frame(b)  # 0.3.8: 情境 - what the one Start starts; fixed while a session runs
        self._tx(ttk.Label(scene_row), "gui_scene").pack(side="left", padx=(0, 6))
        self.v_scene = tk.StringVar(value=scene_mode_name(self.settings))
        # one readonly dropdown: exactly one scene is active at a time; the box shows the UI-language names
        self.scene_combo = ttk.Combobox(scene_row, state="readonly", width=24, values=self._scene_labels())
        self.scene_combo.set(t("gui_scene_" + self.v_scene.get()))
        self.scene_combo.pack(side="left")
        self.scene_combo.bind("<<ComboboxSelected>>", lambda _e: self._scene_changed())
        # #1544: what the video scene listens to, in plain words, one click to switch (was only under 調整)
        self.source_row = ttk.Frame(b)  # its own row: next to the scene dropdown it pushed the bar past its max width
        self.btn_source = ttk.Button(self.source_row, command=self._source_toggled)
        self.btn_source.pack(side="left")
        self.toggle_row = row1 = ttk.Frame(b)
        self.v_mic = tk.BooleanVar(value=True)
        self.btn_mic = ttk.Checkbutton(row1, variable=self.v_mic, style="Toolbutton", command=self._mic_toggled)
        self.btn_mic.pack(side="left")
        self._tx(ttk.Checkbutton(row1, variable=self.v_bar, style="Toolbutton", command=self._bar_toggled),
                 "gui_overlay_btn").pack(side="left", padx=(6, 0))
        # 0.3.8: speaker labels (both scenes); on the toggles row, not the scene row, so the window stays <= 560 px (#1028)
        self.v_spk = tk.BooleanVar(value=bool(self.settings.get("speaker_id")))
        self._tx(ttk.Checkbutton(row1, variable=self.v_spk, command=self._spk_toggled), "gui_spk").pack(
            side="left", padx=(10, 0))
        # #1020: "Freeze subtitles" only freezes this list and the floating bar (translation keeps running)
        self.action_row = row2 = ttk.Frame(b)
        self.bar_wide = None
        self._bar_layout(0)  # narrow until the first <Configure> says otherwise
        b.bind("<Configure>", lambda e: self._bar_layout(e.width), add="+")
        self.btn_adjust = self._tx(ttk.Button(row2, command=self.toggle_adjust), "gui_adjust")
        self.btn_adjust.pack(side="left")
        self.dot = tk.Canvas(row2, width=12, height=12, highlightthickness=0, bd=0)
        self.dot_oval = self.dot.create_oval(1, 1, 11, 11, outline="")
        self.dot.pack(side="left", padx=8)
        self.set_dot("idle")
        self._dot_tip(self.dot)
        self.btn_pause = ttk.Button(row2, text=t("gui_pause"), command=self.toggle_pause)
        self.btn_pause.pack(side="left")
        self.btn = self.both_btn = ttk.Button(row2, text=t("gui_start"), command=lambda: self.toggle(None),
                                              style="Start.Accent.TButton", width=14)
        self.btn.pack(side="right")
        self._mic_label()
        self._sync_quick()

    def _bar_layout(self, width: int):
        """#1241: >= BAR_WIDE_PX the 情境 row and the toggles share one row (a wide window had them stacked at the
        left with the right side empty); narrower, they stack. The action row (Adjust / Freeze / Start) spans both."""
        wide = int(width) >= BAR_WIDE_PX
        one_row = int(width) >= BAR_ONE_ROW_PX  # wide enough: scene, toggles and Adjust / Freeze / Start on ONE row
        if (wide, one_row) == self.bar_wide:
            return
        self.bar_wide = (wide, one_row)
        if one_row:
            self.scene_row.grid(row=0, column=0, columnspan=1, sticky="w", pady=0, padx=(0, 24))
            self.toggle_row.grid(row=0, column=1, columnspan=1, sticky="w", pady=0)
            self.source_row.grid(row=0, column=2, columnspan=1, sticky="w", pady=0, padx=(12, 0))
            self.action_row.grid(row=0, column=3, columnspan=1, sticky="e", pady=0)
        elif wide:  # dense: source toggle shares a row with Adjust / Freeze / Start
            self.scene_row.grid(row=0, column=0, columnspan=1, sticky="w", pady=0, padx=(0, 24))
            self.toggle_row.grid(row=0, column=1, columnspan=1, sticky="w", pady=0)
            self.source_row.grid(row=1, column=0, columnspan=1, sticky="w", pady=(4, 0))
            self.action_row.grid(row=1, column=1, columnspan=1, sticky="e", pady=(4, 0))
        else:
            self.scene_row.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 3), padx=0)
            self.toggle_row.grid(row=1, column=0, columnspan=2, sticky="we", pady=0)
            self.source_row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(3, 0))
            self.action_row.grid(row=3, column=0, columnspan=2, sticky="we", pady=(4, 0))
        self._source_sync()  # #1544: the grid() above would show the row again in the meeting scene

    @staticmethod
    def _scene_labels():
        return [t("gui_scene_" + sc) for sc in SCENES]

    def _live_source_settings(self) -> dict:
        """#1544: the settings with the Source as the Advanced box currently shows it (v_source is the live value;
        collect() copies it back into settings at Start, so the button and the box must read / write the same one)."""
        v = getattr(self, "v_source", None)
        return dict(self.settings, source=v.get()) if v is not None else self.settings

    def _source_sync(self):
        """#1604: the situation alone sets the capture source, so the separate source button stays hidden (a saved
        Source "mic" still applies; it is edited under 調整). The mic toggle is shown for two-way situations only."""
        self.source_row.grid_remove()
        if mic_toggle_visible(self.settings):
            if not self.btn_mic.winfo_manager():
                first = self.btn_mic.master.pack_slaves()
                self.btn_mic.pack(side="left", **({"before": first[0]} if first else {}))
        else:
            self.btn_mic.pack_forget()

    def _source_toggled(self):
        """#1544: one click switches PC sound <-> microphone (applies at the next Start)."""
        new = toggled_source(self._live_source_settings())
        self.settings["source"] = new
        if getattr(self, "v_source", None) is not None:
            self.v_source.set(new)  # the Advanced box follows; Start reads this value (collect)
        save_settings(self.settings)
        self._source_sync()

    def _scene_changed(self):
        """0.3.8: remember the scene picked in the dropdown (applies at the next Start; disabled while running)."""
        idx = self.scene_combo.current()
        if idx < 0:
            return
        self.v_scene.set(SCENES[idx])
        self.settings["scene"] = SCENES[idx]
        save_settings(self.settings)
        self._source_sync()

    def _spk_toggled(self):
        """0.3.8: 標示說話者. On + pack missing -> ask, download in a worker thread (Tk never blocks); the labels start
        with the next Start. Declined / failed -> back to off."""
        from ..models import models_dir
        from . import speaker_pack

        def start():
            self.downloading = "speaker"
            self.start_dl = StartDownload("speaker")
            self.v_dl_pct.set(0)
            self.dl_bar.pack(anchor="w", pady=(4, 0))
            self._status_text(t("gui_dl_running", model="speaker"))
            self._spk_fail_reason = ""
            threading.Thread(target=speaker_pack.run_download, args=(self.q.put, models_dir(), None), daemon=True).start()

        want = self.v_spk.get()
        if want and self.downloading:  # another download is running: not now
            self.v_spk.set(False)
            return
        try:
            res = speaker_pack.on_toggle(want, models_dir(), lambda txt, ok, no: messagebox.askyesno(
                t("gui_spk"), txt, parent=self.root), start, ui_lang())
        except Exception:  # noqa: BLE001
            _diag.swallowed(__name__)
            res = "off"
        if res == "downloading":
            return  # saved when the download succeeded (_spk_done)
        self.v_spk.set(res == "on")
        self.settings["speaker_id"] = res == "on"
        save_settings(self.settings)
        self._spk_sync()

    def _spk_done(self, rc: int):
        from ..models import models_dir
        from . import speaker_pack

        self.start_dl = None
        self.dl_bar.pack_forget()
        self.downloading = None
        self._sync_quick()
        ok = rc == 0 and not any(speaker_pack.missing(models_dir()))
        import_failed = ok and not speaker_pack.import_ok(models_dir())  # installed but sherpa_onnx will not load
        ok = ok and not import_failed
        wanted = bool(self.v_spk.get())  # the user may have switched it off while the download was running
        if ok and not wanted:
            self._status_text(t("gui_dl_ok", model="speaker"))  # pack stays installed; nothing is enabled / attached
            return
        self.v_spk.set(ok)
        self.settings["speaker_id"] = ok
        save_settings(self.settings)
        if ok:
            self._spk_sync()  # live: the running session starts labelling now
            self._status_text(t("gui_dl_ok", model="speaker"))
            return
        self._status_text("")
        if not wanted:
            return
        msg = t("gui_spk_import" if import_failed else "gui_spk_fail")
        reason = getattr(self, "_spk_fail_reason", "")
        if reason and not import_failed:
            msg = f"{msg}\n\n{reason}"
        messagebox.showwarning(t("gui_spk"), msg, parent=self.root)

    def _mic_label(self):
        self.btn_mic.configure(text=t("gui_mic_on" if self.v_mic.get() else "gui_mic_off"))

    @property
    def mic_off(self) -> bool:
        return self.mic_ev.is_set()

    def _mic_toggled(self):
        """#1028: mic off = the speak leg's capture is fed silence (models stay loaded); the listen leg keeps
        running. Needs no restart, so it is not a #983 start-time setting."""
        if self.v_mic.get():
            self.mic_ev.clear()
        else:
            self.mic_ev.set()
        self._mic_label()
        if self.listening and self.load_t0 is None and self.quick_mode != WATCH_MODE:
            cur, note = self.v_notice.get(), t("gui_mic_off_note")
            if self.mic_off:
                if note not in cur:  # keep an existing notice (no CABLE ...), add the mic line under it
                    self.v_notice.set(f"{cur}\n{note}" if cur else note)
            elif note in cur:  # never wipe an unrelated notice
                self.v_notice.set(cur.replace("\n" + note, "").replace(note, "").strip())

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
        """#1001: the bar's Start = the one Start (listen + speak together); Stop while running."""
        self.toggle(None)

    def _pair_changed(self):
        """#981: same language on both sides is said at once, under the pickers, before Start is pressed."""
        s = {"lang": lang_from_label(self.v_lang.get()), "target": self.v_target.get()}
        try:
            quick_settings(s, "listen")
        except SameLanguageError as e:
            self.v_pair.set(t(e.key))
            self.same_lang = True
            self._lang_check_ui()
            return
        self.same_lang = False
        self._lang_check_ui()
        self.v_pair.set("")
        if getattr(self, "ru_notice", None) is not None:  # Russian picked: offer the voice now, not only at Start
            self.root.after(200, self._ru_voice_check)

    same_lang = False

    def _ru_voice_check(self):
        """Either picker just became Russian: when the Russian voice (Piper + engine) is missing, ask for the one-click
        download at once. "Not now" leaves a lasting notice with a [Download] button (it never looks broken)."""
        from ..models import models_dir
        from . import voice_install

        try:
            ru = voice_install.ru_selected(self.v_target.get(), lang_from_label(self.v_lang.get()))
            missing = ru and not self.same_lang and voice_install.piper_missing(models_dir())
        except Exception:  # noqa: BLE001  # an unreadable path must not break the pickers
            return
        if not missing:
            self.ru_notice.grid_remove()
            return
        if self.downloading or self.listening:
            return
        self._ru_voice_ask()

    def _ru_voice_ask(self):
        """The question + download for the Russian voice (no Start follows it); the notice stays when declined."""
        from ..models import models_dir
        from . import voice_install

        if self.downloading:
            return
        if not voice_install.piper_missing(models_dir()):
            self.ru_notice.grid_remove()
            return
        if not self._offer_voice({"voice": True, "target": "ru"}, None, resume=False):
            self.ru_notice_lbl.configure(text=voice_install.notice_text(ui_lang()))
            self.ru_notice_btn.configure(text=voice_install.notice_button(ui_lang()))
            self.ru_notice.grid()
        else:
            self.ru_notice.grid_remove()

    def _lang_check_ui(self):
        """#982: My language == Other side language: inline error and Start disabled (home button and bar button)."""
        if getattr(self, "lang_err", None) is not None:
            self.lang_err.configure(text=t("gui_same_lang") if self.same_lang else "")
            if self.same_lang:  # an empty error line is a ~20 px blank band, so it only takes space when it has text
                self.lang_err.grid()
            else:
                self.lang_err.grid_remove()
        if hasattr(self, "btn"):
            self._sync_quick()

    def set_dot(self, name: str):
        self.dot_state = name
        self.dot.itemconfigure(self.dot_oval, fill=DOT[name])

    def _dot_tip(self, w):
        """#1604: the status dot says what it means on hover (it had no label)."""
        tip = {"w": None}

        def show(_e=None):
            hide()
            x, y = w.winfo_rootx() + 14, w.winfo_rooty() + w.winfo_height() + 4
            top = tk.Toplevel(w)
            top.wm_overrideredirect(True)
            top.wm_geometry(f"+{x}+{y}")
            ttk.Label(top, text=t("gui_dot_" + self.dot_state), padding=(6, 2), relief="solid").pack()
            tip["w"] = top

        def hide(_e=None):
            if tip["w"] is not None:
                tip["w"].destroy()
                tip["w"] = None

        w.bind("<Enter>", show)
        w.bind("<Leave>", hide)

    def _show_panel(self):
        """Raise the main window (the status and notice lines live there, #1028)."""
        self.root.deiconify()
        self.root.lift()

    def toggle_adjust(self):
        if self.panel.state() == "normal":
            self._remember_panel()
            self.panel.withdraw()
        else:
            self.panel.deiconify()
            self._apply_panel_geometry(flush=True)  # #1556: the opening flush runs under the measuring guard too
            self.panel.lift()

    def _apply_panel_geometry(self, flush=False):
        """#939: minsize = the one-column width x the two-column height; restore the remembered size / position
        (never below minsize). The settings grid shows two columns whenever the window is wide enough."""
        p, s = self.panel, self.settings
        self._measuring_panel = True  # #1556: ignore <Configure> events caused by the measuring re-grids below
        try:
            if flush:
                p.update_idletasks()
            self._set_setting_cols(1)
            p.update_idletasks()
            mw = p.winfo_reqwidth()
            self._set_setting_cols(2)
            p.update_idletasks()
            self.two_col_w = self.frm.winfo_reqwidth()
            mh = p.winfo_reqheight()
        finally:
            self._measuring_panel = False
        p.minsize(mw, mh)
        self.panel_min = (mw, mh)
        w, h = s.get("panel_w"), s.get("panel_h")
        geo = f"{max(w, mw)}x{max(h, mh)}" if w and h else ""
        x, y = s.get("panel_x"), s.get("panel_y")
        if x is not None and y is not None:
            geo += f"+{x}+{y}"
        if geo:
            p.geometry(geo)

    def _remember_panel(self):
        p = self.panel
        if p.state() != "normal":
            return
        self.settings.update(panel_w=p.winfo_width(), panel_h=p.winfo_height(), panel_x=p.winfo_x(), panel_y=p.winfo_y())

    def _build_controls(self):
        s = self.settings
        self.panel = p = tk.Toplevel(self.root)  # #455: the "⚙ Adjust" panel, hidden until the bar asks for it
        p.withdraw()
        p.title("tinglongwu")
        p.resizable(True, True)  # #939: a normal resizable window; size and position are remembered
        p.protocol("WM_DELETE_WINDOW", p.withdraw)
        self._build_sidebar(p)
        self.body = body = ttk.Frame(p, padding=(12, 3, 12, 3))
        body.pack(side="left", fill="both", expand=True)
        short = p.winfo_screenheight() < SHORT_SCREEN_PX  # #886: a 768 px screen has no height to spare
        self.settings_title = self._tx(ttk.Label(body, font=(FONT_FAMILY, 14, "bold")), "gui_menu_settings")
        self.settings_title.pack(anchor="w", pady=(0, 2 if short else 4))
        card = "Card.TFrame" if sv_ttk is not None else "TFrame"

        def group(title_key):  # #1028: one plain-language group = a bold heading + its choices
            g = ttk.Frame(body, style=card, padding=(10, 3 if short else 5, 10, 3 if short else 5))
            g.pack(fill="x", pady=(0, 3))
            self._tx(ttk.Label(g, font=(FONT_FAMILY, 11, "bold")), title_key).pack(anchor="w")
            return g

        g_speed, g_appear = group("gui_set_speed"), group("gui_set_appear")
        g_mine, g_rec = group("gui_set_mine"), group("gui_set_records")
        self.card_adv = ttk.Frame(body, style=card, padding=(ADV_PAD_X // 2, 4, ADV_PAD_X // 2, 4))  # the Advanced toggle + everything else
        self.card_adv.pack(fill="x", pady=(0, 0))
        self.frm = f = ttk.Frame(self.card_adv, padding=(0, 4, 0, 0))
        f.columnconfigure(3, minsize=14)  # #886: gap between the two setting columns
        for col in (1, 5):  # the two control columns share the card's width (the "..." buttons stay narrow)
            f.columnconfigure(col, weight=1)
        self.slot_widgets, self.setting_cols, self.two_col_w = {}, SETTING_COLS, 0  # #939: 2 -> 1 column wrap
        self.card_adv.bind("<Configure>", lambda e: self._adv_width_changed(e.width - ADV_PAD_X)
                           if e.widget is self.card_adv else None)
        self.combos = {}
        self.kind_combos = {}
        self.v_lang = self._combo(0, "gui_src_lang", "lang", lang_labels())  # ja/en/ru first, others marked (#58)
        self.v_lang.set(lang_label(self.settings["lang"]))
        self.v_target = self._combo(1, "gui_target", "target", TARGETS, "target")
        self.v_quality = self._combo(2, "gui_quality", "quality", QUALITIES, "quality")
        row = ttk.Frame(g_speed)
        row.pack(anchor="w")
        for val, key in (("fast", "gui_set_fast"), ("auto", "gui_set_auto"), ("quality", "gui_set_accurate")):
            self._tx(ttk.Radiobutton(row, variable=self.v_quality, value=val), key).pack(side="left", padx=(0, 12))
        # #1385: model in use + download size, on the Speed buttons' own row so it adds no height (#886: Advanced
        # open must still fit a 1366x768 screen)
        self.lbl_asr = ttk.Label(row, wraplength=520, justify="left")
        self.lbl_asr.pack(side="left", padx=(4, 0))
        self.speech_hw = None  # #1385: (cuda, hwprobe result), probed once on the first hint
        self.v_engine = self._combo(11, "gui_engine", "engine", engine_labels())  # #399: under Advanced
        self.v_engine.set(next((l for l in engine_labels() if engine_from_label(l) == s["engine"]), s["engine"]))
        self.v_adv = tk.BooleanVar(value=False)
        self.v_model = self._combo(3, "gui_model", "model", model_labels(ram_gb=self._cpu_ram()))
        self.v_model.set(label_for_model(self.settings["model"], ram_gb=self._cpu_ram()))
        self.combos["model"].configure(width=combo_width(model_labels(ram_gb=self._cpu_ram())))
        self.combos["model"].bind("<<ComboboxSelected>>", lambda _e: self._model_picked())
        self.downloading = None
        self.v_quality.trace_add("write", lambda *_: self._quality_changed())
        self._quality_changed()
        self.v_source = self._combo(4, "gui_source", "source", SOURCES, "source")
        self.v_source.trace_add("write", lambda *_: self._source_sync())  # #1544: the video button follows the box
        self._build_send_mode(g_mine).pack(anchor="w", fill="x")
        self.v_preset = self._combo(5, "gui_mode", "preset", PRESETS, "preset")
        self._label(6, "gui_glossary")
        self.v_gloss = tk.StringVar(value=s["glossary"])
        self._put(ttk.Entry(f, textvariable=self.v_gloss, width=16), 6, 1, sticky="we", pady=1, padx=(6, 0))
        self._put(ttk.Button(f, text="...", width=3, command=self.pick_glossary), 6, 2, padx=(4, 0))
        self.v_save = tk.BooleanVar(value=s["save"])
        self._tx(ttk.Checkbutton(g_rec, variable=self.v_save), "gui_save").pack(anchor="w")
        self._label(7, "gui_save_path")
        self.v_save_path = tk.StringVar(value=s["save_path"])
        self._put(ttk.Entry(f, textvariable=self.v_save_path, width=16), 7, 1, sticky="we", pady=1, padx=(6, 0))
        self._put(ttk.Button(f, text="...", width=3, command=self.pick_save), 7, 2, padx=(4, 0))
        self.var_show_src = tk.BooleanVar(value=s["show_src"])
        self._tx(ttk.Checkbutton(g_rec, variable=self.var_show_src, command=self._src_toggled),
                 "gui_show_src_line").pack(anchor="w")
        self.v_live = tk.BooleanVar(value=s["live_draft"])  # #1028: subtitle mode = live draft on / off
        for val, key in ((False, "gui_set_sentence"), (True, "gui_set_live")):
            self._tx(ttk.Radiobutton(g_appear, variable=self.v_live, value=val), key).pack(anchor="w")
        self.v_voice = tk.BooleanVar(value=s["voice"])  # #330: voice into earphones, never the captured device
        self._tx(ttk.Checkbutton(g_mine, variable=self.v_voice), "gui_voice").pack(anchor="w")
        self._label(10, "gui_voice_device")
        self.voice_default = t("gui_voice_default")
        outs = voice_outputs()
        shown = s["voice_device"] if s["voice_device"] in outs or not outs else ""  # #448: unplugged -> default
        self.v_voice_dev = tk.StringVar(value=shown or self.voice_default)
        self._put(ttk.Combobox(f, textvariable=self.v_voice_dev, values=[self.voice_default] + outs,
                               state="readonly", width=18), 10, 1, columnspan=2, sticky="we", pady=1, padx=(6, 0))
        self._label(12, "gui_listen_device")  # #1070: which output the other side is heard on (Advanced)
        self.listen_default = t("gui_listen_default")
        louts = listen_outputs()
        lshown = s.get("listen_device", "") if s.get("listen_device", "") in louts else ""
        self.v_listen_dev = tk.StringVar(value=lshown or self.listen_default)
        self._put(ttk.Combobox(f, textvariable=self.v_listen_dev, values=[self.listen_default] + louts,
                               state="readonly", width=18), 12, 1, columnspan=2, sticky="we", pady=1, padx=(6, 0))
        # #894: which microphone the Microphone source (room sound) records; slot 13 sits beside "Listen to output"
        # in the two-column layout, so the panel gets no taller there
        self._label(13, "gui_mic_device")
        self.mic_default = t("gui_mic_default")
        mics = mic_inputs()
        mshown = s.get("mic_device", "") if s.get("mic_device", "") in mics else ""  # unplugged -> default
        self.v_mic_dev = tk.StringVar(value=mshown or self.mic_default)
        self._put(ttk.Combobox(f, textvariable=self.v_mic_dev, values=[self.mic_default] + mics,
                               state="readonly", width=18), 13, 1, columnspan=2, sticky="we", pady=1, padx=(6, 0))
        # #1531: "Use GPU: Auto / Off" is the only CPU/GPU control (the installer no longer asks)
        # (built as slot 15, then moved beside Check setup: a new settings row made Advanced too tall for 1366x768)
        self.v_gpu_use = self._combo(15, "gui_gpu_use", "gpu_use", ("auto", "off"), "gpu_use")  # raw auto/off, named per UI language
        self.v_gpu_use.trace_add("write", lambda *_: self._asr_hint())  # Off -> the CPU tier's speech model
        self._asr_hint()  # the first hint already follows a saved Off
        # #738: subtitle lines / font size, UI language, dark theme, updates, report, about live in the menu bar
        self.v_lines = self._num_var(ROWS_MIN, ROWS_MAX, s["overlay_lines"], lambda n: self.overlay.set_rows(n))
        self.v_font = self._num_var(FONT_MIN, FONT_MAX, s["font_size"], lambda n: self.overlay.set_font(n))
        # Check setup stays in the panel, above the subtitle log (#756); Report a problem and the update controls
        # moved to the menu bar's Help menu (#738)
        self.act_row = act = ttk.Frame(f)
        act.grid(row=UPDATE_ROW, column=0, columnspan=SPAN, sticky="we", pady=(6, 0))
        act.columnconfigure(0, weight=1)
        self._tx(ttk.Button(act, command=self.check_setup), "gui_check_setup").grid(row=0, column=0, sticky="we")
        for col, (w, _d) in enumerate(self.slot_widgets.pop(15), start=1):  # label, then the Auto / Off box
            w.grid(in_=act, row=0, column=col, columnspan=1, sticky="e", padx=(10, 0), pady=0)
        self.v_auto_upd = tk.BooleanVar(value=autoupdate.auto_on(userdirs.read_config()))
        self.v_status = tk.StringVar(value=t("gui_ready"))
        self.status_box = sb = ttk.Frame(self.status_holder, padding=(0, 2, 0, 0))  # status/notice: main window (#1028)
        self.lbl_status = ttk.Label(sb, textvariable=self.v_status, wraplength=MAIN_W - 40, justify="left")
        self.lbl_status.pack(anchor="w", fill="x")
        self.v_dl_pct = tk.IntVar(value=0)  # #739: first-Start download progress bar (hidden unless a download runs)
        self.dl_bar = ttk.Progressbar(sb, variable=self.v_dl_pct, maximum=100, length=300)
        self.start_dl = None
        self.v_notice = tk.StringVar(value="")
        self.lbl_notice = ttk.Label(sb, textvariable=self.v_notice, wraplength=MAIN_W - 40, foreground="#b35a00",
                                    justify="left")
        self._tok(self.lbl_notice, "warn")
        self._tok(self.lbl_status, "muted")
        self.lbl_notice.pack(anchor="w", fill="x")
        sb.bind("<Configure>", lambda e: self._wrap_status(e.width), add="+")  # #1241
        self._build_send_gate(sb)  # #833: in the always-visible status box, not under the collapsed Advanced
        self.v_ui_lang = tk.StringVar(value=next(k for k, v in UI_LANGS.items() if v == ui_lang()))
        if sv_ttk is not None:
            self.v_dark = tk.BooleanVar(value=self.settings.get("theme", "dark") != "light")
        self._build_log(self.chat_holder)

    # -- #698 auto-update -------------------------------------------------------------------------
    def _build_update_line(self):
        """One non-blocking line under the bar: "New version ... [Update now] [Later]". Hidden until needed."""
        self.upd_ver = None  # newer version seen this run
        self.upd_exe = None  # verified installer path once downloaded
        self.upd_busy = False  # a check or download child is running
        self.upd_msg: tuple | None = None  # (i18n key, format kwargs, button keys)
        u = self.upd_frame = ttk.Frame(self.root, padding=(4, 0, 4, 2))
        self.v_upd = tk.StringVar()
        _l = ttk.Label(u, textvariable=self.v_upd, wraplength=UPDATE_LINE_WRAP_PX)
        _l.pack(side="left")
        # one line when the window is wide enough; wraps only as the width runs out (text + buttons ~ 300 px)
        u.bind("<Configure>", lambda e, l=_l: l.configure(wraplength=max(UPDATE_LINE_WRAP_PX, e.width - 300)), add="+")
        self.upd_no = ttk.Button(u, command=self._update_later, style="Bar.TButton", width=0)
        self.upd_yes = ttk.Button(u, command=self._update_yes, style="Bar.TButton", width=0)
        self.upd_apply_when_ready = False

    # -- #1525: GPU speed-up pack offer ---------------------------------------------------------------
    def _build_gpu_offer(self):
        """One non-blocking line: "NVIDIA GPU found - install speed-up pack? [Install] [Not now] [Don't ask again]".
        Hidden unless gpu_offer.should_offer; nvidia-smi runs on a thread, never on the Tk thread."""
        from . import gpu_offer

        self.gpu_busy = False
        self.gpu_lines: list[str] = []
        g = self.gpu_frame = ttk.Frame(self.root, padding=(4, 0, 4, 2))
        self.v_gpu = tk.StringVar()
        _l = ttk.Label(g, textvariable=self.v_gpu, wraplength=UPDATE_LINE_WRAP_PX)
        _l.pack(side="left")
        # one line when the window is wide enough; wraps only as the width runs out (text + buttons ~ 300 px)
        g.bind("<Configure>", lambda e, l=_l: l.configure(wraplength=max(UPDATE_LINE_WRAP_PX, e.width - 300)), add="+")
        self.gpu_bar = ttk.Progressbar(g, mode="indeterminate", length=80)
        self.gpu_btns = [self._tx(ttk.Button(g, command=c, style="Bar.TButton", width=0), k) for k, c in
                         (("gpu_offer_never", self._gpu_never), ("gpu_offer_later", self._gpu_hide),
                          ("gpu_offer_install", self._gpu_install))]
        self.gpu_auto = None  # #1736: gpu_offer.AutoInstall while the background download runs
        self.gpu_cancel_btn = self._tx(ttk.Button(g, command=self._gpu_auto_cancel, style="Bar.TButton", width=0),
                                       "gpu_auto_cancel")
        probe = not gpu_offer.offline() and not gpu_offer.cpu_mode()

        def work():  # nvidia-smi once, off the Tk thread; also fills the CPU-mode wording cache (core.nvidia_seen)
            from .core import nvidia_seen

            nvidia_seen()
            if probe:
                st = self._gpu_detect()
                self.q.put(("gpu_state", st))
                if st == gpu_offer.UNAVAILABLE:  # #1775: no NVIDIA pack to offer -> maybe the Vulkan pack (AMD / Intel)
                    self.q.put(("gpu_vk_state", self._vk_detect()))

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _gpu_detect() -> str:
        from . import gpu_offer

        try:
            return gpu_offer.detect()
        except Exception:  # noqa: BLE001 - no probe: no banner
            return gpu_offer.UNAVAILABLE

    @staticmethod
    def _vk_detect() -> str:
        from . import vulkan_offer

        try:
            return vulkan_offer.detect()
        except Exception:  # noqa: BLE001 - no probe: no banner
            return vulkan_offer.UNAVAILABLE

    def _gpu_show(self, text: str, buttons: bool, busy: bool = False, cancel: bool = False):
        self.v_gpu.set(text)
        for b in self.gpu_btns:
            b.pack_forget()
        self.gpu_cancel_btn.pack_forget()
        if cancel:
            self.gpu_cancel_btn.pack(side="right", padx=(4, 0))
        if buttons:
            for b in self.gpu_btns:
                b.pack(side="right", padx=(4, 0))
        if busy:
            self.gpu_bar.pack(side="right", padx=(4, 0))
            self.gpu_bar.start(15)
        else:
            self.gpu_bar.stop()
            self.gpu_bar.pack_forget()
        if not self.gpu_frame.winfo_ismapped():
            self.gpu_frame.pack(fill="x")

    def _gpu_hide(self):
        self.gpu_bar.stop()
        self.gpu_frame.pack_forget()

    def _gpu_offer_show(self):
        from . import gpu_offer

        self._gpu_show(t("gpu_offer_banner", size=gpu_offer.GPU_PACK_SIZE), buttons=True)

    def _gpu_never(self):
        from . import gpu_offer, vulkan_offer

        self.settings[vulkan_offer.DISMISS_KEY if getattr(self, "gpu_vk", False) else gpu_offer.DISMISS_KEY] = True
        save_settings(self.settings)
        self._gpu_hide()

    def _vk_install(self):
        """#1775: one click = Vulkan pack + whisper.cpp model + probe (vulkan_offer.install_cmds / run_install)."""
        from . import vulkan_offer
        from .core import run_pack_download

        if self.gpu_busy or vulkan_offer.INSTALLING.is_set():
            return
        vulkan_offer.INSTALLING.set()
        self.gpu_busy, self.gpu_lines = True, []
        self._gpu_show(t("vk_offer_running"), buttons=False, busy=True)
        put = lambda ev: self.q.put(("gpu_" + ev[0], *ev[1:]))  # noqa: E731
        try:
            threading.Thread(target=vulkan_offer.run_install, args=(run_pack_download, put, vulkan_offer.install_cmds()),
                             daemon=True).start()
        except RuntimeError:
            vulkan_offer.INSTALLING.clear()
            self.gpu_busy = False
            self._gpu_show(t("vk_offer_banner"), buttons=True)

    def _gpu_auto_cancel(self):
        """#1736: Cancel on the background-download line: stop the child, remember it, show the one-click offer."""
        if self.gpu_auto is not None:
            self.gpu_auto.cancel()

    def _gpu_install(self, auto: bool = False):
        from . import gpu_offer
        from .core import run_pack_download

        if getattr(self, "gpu_vk", False):
            return self._vk_install()
        if self.gpu_busy or gpu_offer.INSTALLING.is_set() or gpu_offer.offline():
            return
        from ..gpu_pack import pack_dir

        pack = pack_dir()
        failed_at = getattr(self, "gpu_runtime_failed_mtime", None)  # pack replaced since the failure -> trust it
        if (pack is not None and (not getattr(self, "gpu_runtime_failed", False) or failed_at != _mtime(pack))
                and gpu_offer._pack_usable(pack)):  # installed meanwhile (e.g. Add-ons); not after a CUDA load failure
            self._gpu_show(t("gpu_offer_ok"), buttons=False)
            return
        gpu_offer.INSTALLING.set()
        self.gpu_busy, self.gpu_lines = True, []
        put = lambda ev: self.q.put(("gpu_" + ev[0], *ev[1:]))  # noqa: E731 - not the language-pack events
        if auto:  # #1736: no click; one line with size + progress + Cancel, listening keeps running on the CPU
            self.gpu_auto = gpu_offer.AutoInstall(run_pack_download, self.settings, lambda: save_settings(self.settings))
            self._gpu_show(t("gpu_auto_running", size=gpu_offer.GPU_PACK_SIZE), buttons=False, busy=True, cancel=True)
            target = self.gpu_auto.run_install
        else:
            self.gpu_auto = None
            self._gpu_show(t("gpu_offer_running", size=gpu_offer.GPU_PACK_SIZE), buttons=False, busy=True)
            target = lambda put, **kw: gpu_offer.run_install(run_pack_download, put, **kw)  # noqa: E731
        try:
            threading.Thread(target=target, args=(put,), kwargs={"cmd": gpu_offer.gpu_pack_cmd()}, daemon=True).start()
        except RuntimeError:  # no thread: release the lock and offer Install again
            gpu_offer.INSTALLING.clear()
            self.gpu_busy = False
            self.gpu_auto = None
            self._gpu_offer_show()

    def _gpu_event(self, ev):
        from . import gpu_offer

        kind = ev[0]
        if kind == "gpu_state":
            if not self.gpu_busy and gpu_offer.should_offer(ev[1], gpu_offer.cpu_mode(), bool(self.settings.get(gpu_offer.DISMISS_KEY)),
                                      gpu_offer.offline()):
                if gpu_offer.should_autostart(ev[1], gpu_offer.cpu_mode() or not self._gpu_allowed(), bool(self.settings.get(gpu_offer.DISMISS_KEY)),
                                              gpu_offer.offline(), bool(self.settings.get(gpu_offer.AUTO_OFF_KEY))):
                    self._gpu_install(auto=True)  # #1736: download by default, the user can cancel
                else:
                    self._gpu_offer_show()
        elif kind == "gpu_vk_state":
            from . import vulkan_offer

            if not self.gpu_busy and vulkan_offer.should_offer(ev[1], False, bool(self.settings.get(vulkan_offer.DISMISS_KEY)),
                                                               gpu_offer.offline()):
                self.gpu_vk = True
                self._gpu_show(t("vk_offer_banner"), buttons=True)
        elif kind == "gpu_pack_line":
            self.gpu_lines = [*self.gpu_lines[-20:], ev[1]]
        elif kind == "gpu_pack_done":
            self.gpu_busy = False
            auto, self.gpu_auto = self.gpu_auto, None
            if auto is not None and auto.outcome(ev[2], self.gpu_lines) == "cancelled":  # #1736: stays on CPU, offer remains
                self._gpu_offer_show()
            elif getattr(self, "gpu_vk", False):  # #1775: the Vulkan pack has its own wording (no CUDA restart notice)
                self._gpu_show(t("vk_offer_ok") if ev[2] == 0 else t("vk_offer_fail", reason=gpu_offer.fail_reason(self.gpu_lines)),
                               buttons=False)
            elif gpu_offer.install_ok(ev[2], self.gpu_lines):
                self.gpu_runtime_failed = False  # a fresh pack replaced the one that failed to load
                self._gpu_show(t("gpu_offer_ok"), buttons=False)
            else:
                self._gpu_show(t("gpu_offer_fail", reason=gpu_offer.fail_reason(self.gpu_lines)), buttons=False)

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

    def _apply_pending_update(self) -> bool:
        """#779: a verified installer downloaded in the background last time is applied before the user starts
        working: one short status line, no question. True when the apply helper was started."""
        if autoupdate.is_wizard_install():
            autoupdate.cleanup_old()  # #946: *.old left by an app-layer update; this start succeeded
        if not autoupdate.is_wizard_install() or autoupdate.offline():
            return False
        path = autoupdate.pending_to_apply(userdirs.read_config(), __version__)
        if not path:
            return False
        self.upd_exe = path
        autoupdate.clear_pending()  # a failed apply must not loop on every launch
        self._apply_update()
        return True

    def _auto_update_check(self):
        if not autoupdate.can_update():
            return
        if autoupdate.is_wizard_install() and self._apply_pending_update():
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
        if autoupdate.is_git_checkout():  # #1672: Linux / git-checkout macOS follow auto_update.sh
            threading.Thread(target=autoupdate.run_git_check, args=(self.q.put, manual, __version__), daemon=True).start()
            return
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
        if self.upd_msg and self.upd_msg[0] == "gui_upd_fail_check":
            import webbrowser

            webbrowser.open(autoupdate.DOWNLOAD_URL)
            self._update_later()
        elif self.upd_exe:
            self._apply_update()
        elif self.upd_ver and not self.upd_busy:
            self._download_update(silent=False)

    def _download_update(self, silent: bool):
        self.upd_busy = True
        self.upd_apply_when_ready = not silent  # "Update now" clicked: install as soon as it is verified
        if not silent:
            self._upd_show("gui_upd_downloading", ver=self.upd_ver, pct=0)
        if autoupdate.is_git_checkout():
            threading.Thread(target=autoupdate.run_git_apply, args=(self.q.put,), daemon=True).start()
            return
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
        elif kind == "upd_fail_check":  # #1526: a failed manual check must not read as "latest"
            self.upd_busy = False
            self.upd_ver = None
            self._upd_show("gui_upd_fail_check", ("gui_upd_open_page", "gui_upd_later"), why=ev[1])
        elif kind == "upd_progress":
            if self.upd_msg and self.upd_msg[0] == "gui_upd_downloading":
                self._upd_show("gui_upd_downloading", ver=self.upd_ver, pct=ev[1])
        elif kind == "upd_ready":
            self.upd_busy = False
            if not self.upd_apply_when_ready and self.upd_ver:  # #779: quiet download -> install at next launch
                autoupdate.set_pending(self.upd_ver, ev[1])
                self.upd_exe = ev[1]  # a manual "Check for updates" now offers the staged installer, no re-download
                if autoupdate.claim_prompt(userdirs.read_config()):  # at most one line per day
                    self._upd_show("gui_upd_staged", ver=self.upd_ver)
                return True
            self.upd_exe = ev[1]
            if self.upd_apply_when_ready:
                self._apply_update()
            else:
                self._upd_show("gui_upd_ready", ("gui_upd_restart", "gui_upd_later"), ver=self.upd_ver)
        elif kind == "upd_git_done":  # #1672
            self.upd_busy = False
            self._upd_show("gui_upd_git_deps" if len(ev) > 1 and ev[1] else "gui_upd_git_done")
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
            rc = autoupdate.start_apply(self.upd_exe)
        except Exception as e:  # noqa: BLE001
            self._upd_show("gui_upd_fail", ("gui_upd_now", "gui_upd_later"), why=type(e).__name__)
            return
        if rc:  # #1348: the file failed its signature / SHA256 re-check; nothing was started, so keep running
            self._upd_show("gui_upd_fail", ("gui_upd_now", "gui_upd_later"), why="verify")
            return
        self.root.after(UPDATE_QUIT_DELAY_MS, self.quit)  # the helper waits for this process, then installs and restarts the GUI

    # -- #738 menu bar: File / Settings / Help hold the rarely used actions ------------------------------------
    def build_menubar(self):
        """(Re)build the menu bar of the bar window and of the settings panel (#939) in the current UI language."""
        self.menubar = self._menubar_for(self.root, "menubar")
        self.panel_menubar = self._menubar_for(self.panel, "panel_menubar")

    def _menubar_for(self, win, attr):
        mb = tk.Menu(win, tearoff=0)
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
        sm.add_command(label=t("addons_menu"), command=self.open_addons)  # #614
        sm.add_command(label=t("gui_menu_usage"), command=self.ask_usage)  # #680
        sm.add_checkbutton(label=t("gui_topics_switch"), variable=self._topics_var(),  # #1379: the one switch
                           command=self._topics_toggled)
        sm.add_command(label=t("gui_topics_menu"), command=self._topics_toggle)  # #611
        self.v_notes_float = tk.BooleanVar(master=self.root, value=notes_float_on(self.settings))  # #1705
        sm.add_checkbutton(label=t("gui_topics_float"), variable=self.v_notes_float, command=self._notes_float_toggled)
        sm.configure(postcommand=self._sync_topics_var)  # #1379: the check mark follows the GPU default
        sm.add_checkbutton(label=t("gui_tech_labels"), variable=self.v_tech, command=self._tech_toggled)  # #1806: in the menu: a panel row made Advanced too tall (#886)
        mb.add_cascade(label=t("gui_menu_settings"), menu=sm)
        hm = tk.Menu(mb, tearoff=0)
        hm.add_command(label=t("gui_menu_tour"), command=self.show_tour)
        if autoupdate.can_update():  # #698 installer installs; #1672 Linux / macOS git checkouts
            hm.add_command(label=t("gui_upd_check"), command=lambda: self._check_update(manual=True))
            hm.add_checkbutton(label=t("gui_upd_auto"), variable=self.v_auto_upd,
                               command=lambda: autoupdate.set_auto(self.v_auto_upd.get()))
        hm.add_command(label=t("gui_report"), command=lambda: self.report_problem())
        hm.add_command(label=t("gui_copy_ai"), command=lambda: self.copy_ai_prompt())  # #940
        hm.add_separator()
        hm.add_command(label=t("gui_menu_about"), command=self.show_about)
        mb.add_cascade(label=t("gui_menu_help"), menu=hm)
        old = getattr(self, attr, None)
        self._menu_row(win, attr, mb)  # #1662: the native Windows menu bar cannot be themed -> an in-window row
        if old is not None:
            old.destroy()
        return mb

    def _menu_row(self, win, attr, mb):
        """#1662: File / Settings / Help as flat menubuttons in a dark row with a 1 px separator under it."""
        row = getattr(self, attr + "_row", None)
        sepname = "menu_sep" if win is self.root else attr + "_sep"
        if row is None or not row.winfo_exists():
            row = ttk.Frame(win, padding=(6, 0, 6, 0))
            sep = tk.Frame(win, height=1, bd=0)
            anchor = self.top_frame if win is self.root else self.settings_title
            row.pack(side="top", fill="x", before=anchor)
            sep.pack(side="top", fill="x", before=anchor)
            setattr(self, attr + "_row", row)
            setattr(self, sepname, sep)
        for w in row.winfo_children():
            w.destroy()
        for i in range(mb.index("end") + 1):
            ttk.Menubutton(row, text=mb.entrycget(i, "label"), style="Menu.TMenubutton",
                           menu=mb.nametowidget(mb.entrycget(i, "menu"))).pack(side="left")
        self._style_menu(mb)
        getattr(self, sepname).configure(bg=self.pal["sep"])

    def _maybe_ask_usage(self):
        if usage_should_ask(self.settings) and getattr(self, "usage_win", None) is None:
            self.ask_usage()

    def ask_usage(self):
        """#680: "what do you mainly use it for?" - a small local dialog; Save stores the choice in gui.json, Skip
        (or closing it) only records that we asked. Reachable again from Settings. Nothing is sent anywhere."""
        old = getattr(self, "usage_win", None)
        if old is not None:  # one dialog at a time: a second one could overwrite a newer answer
            try:
                old.lift()
                old.focus_force()
            except tk.TclError:
                self.usage_win = None
            else:
                return
        win = tk.Toplevel(self.root)
        win.title(t("gui_usage_title"))
        win.transient(self.root)
        v = tk.StringVar(value=self.settings.get("usage_use") or USAGE_CHOICES[0])
        ttk.Label(win, text=t("gui_usage_title")).pack(padx=14, pady=(12, 6), anchor="w")
        for c in USAGE_CHOICES:
            ttk.Radiobutton(win, text=t("gui_usage_" + c), value=c, variable=v).pack(padx=22, anchor="w")
        ttk.Label(win, text=t("gui_usage_note")).pack(padx=14, pady=6, anchor="w")

        def done(choice):
            usage_answer(self.settings, choice)
            self.collect()
            self.usage_win = None
            win.destroy()

        row = ttk.Frame(win)
        row.pack(pady=(0, 10))
        ttk.Button(row, text=t("gui_usage_ok"), command=lambda: done(v.get())).pack(side="left", padx=4)
        ttk.Button(row, text=t("gui_usage_skip"), command=lambda: done(None)).pack(side="left", padx=4)
        win.protocol("WM_DELETE_WINDOW", lambda: done(None))
        self.usage_win = win

    def open_addons(self):
        """#614: Settings > Add-ons (installed / available packs, add, remove)."""
        from .addons_screen import AddonsScreen

        AddonsScreen(self.root)

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
        self.status_box.pack(fill="x")
        # #737: everything else sits under this toggle, collapsed by default (state remembered as advanced_open)
        self.adv_btn = ttk.Checkbutton(self.card_adv, variable=self.v_adv, style="Adv.Toolbutton", command=self._adv_toggled)
        self.adv_btn.pack(anchor="w", fill="x")
        self.adv_hint = self._tx(ttk.Label(self.card_adv, wraplength=300, justify="left"), "gui_set_adv")
        self._tok(self.adv_hint, "muted")
        self.adv_hint.pack(anchor="w")
        self.set_advanced(bool(self.settings["advanced_open"]))
        # #737 / #1006: the pickers are my language (`target`) and the other side's language (= source language
        # `lang`, same variable as tour step 1); quick_settings() turns them into listen: other -> mine, speak: mine -> other.
        q = self.top_frame
        tgt = self.combos["target"]
        self._tx(ttk.Label(q), "gui_quick_target").grid(row=0, column=0, sticky="w")
        self.home_target = ttk.Combobox(q, textvariable=tgt.cget("textvariable"), values=tgt.cget("values"),
                                        state="readonly", width=7)
        self.home_target.grid(row=0, column=1, sticky="w", padx=(4, 8), pady=(0, 2))
        self._tx(ttk.Label(q), "gui_quick_other").grid(row=1, column=0, sticky="w")  # own row: never wider than the window
        self.home_lang = ttk.Combobox(q, textvariable=self.v_lang, values=lang_labels(), state="readonly", width=7)
        self.home_lang.grid(row=1, column=1, sticky="w", padx=(4, 8))
        q.columnconfigure(4, weight=1)  # spare width goes to the right, the pickers stay grouped at the left
        self.v_pair = tk.StringVar()  # #981: "same language" is said at once, before Start
        # #982: the inline error (v_pair only carries the state text)
        self.lang_err = self._tok(ttk.Label(q, wraplength=MAIN_W - 40, justify="left"), "err")
        self.lang_err.grid(row=2, column=0, columnspan=4, sticky="w")
        self.ru_notice = ttk.Frame(q)  # lasting "Russian voice not downloaded" line + [Download]
        self.ru_notice_lbl = self._tok(ttk.Label(self.ru_notice), "warn")
        self.ru_notice_lbl.pack(side="left")
        self.ru_notice_btn = ttk.Button(self.ru_notice, command=self._ru_voice_ask)
        self.ru_notice_btn.pack(side="left", padx=(8, 0))
        self.ru_notice.grid(row=3, column=0, columnspan=4, sticky="w")
        self.ru_notice.grid_remove()
        for v in (self.v_lang, self.v_target):
            v.trace_add("write", lambda *_: self._pair_changed())
        self._pair_changed()
        for w in self.frm.grid_slaves(row=0):
            w.grid_remove()
        # #314 "How to start (?)" is in the menu bar's Help menu (#738) and in the subtitle bar's right-click menu
        # #1001 (owner 2026-11-15): one Start runs listen + speak together; there is no listen / speak choice

    def _quick_phase(self) -> str:
        if self.load_t0 is not None:
            return "loading"
        if self.downloading or (self.listening and self.stop_ev.is_set()):
            return "busy"
        return "running" if self.listening else "idle"

    def _sync_scene(self):
        """0.3.8: the scene can only change while stopped (applies at the next Start)."""
        st = "normal" if self._quick_phase() == "idle" else "disabled"
        cb = getattr(self, "scene_combo", None)
        if cb is not None:
            cb.configure(state="readonly" if st == "normal" else "disabled")
        bs = getattr(self, "btn_source", None)
        if bs is not None:  # #1544: the source applies at the next Start too; follows a change made under 調整
            self._source_sync()
            bs.configure(state=st)

    def _sync_quick(self):
        """Label/enable the first-screen buttons for the current session state (#294)."""
        self._sync_scene()
        if getattr(self, "lbl_restart", None) is not None:
            self._restart_check()  # #983: the notice goes away once the session has stopped
        if not hasattr(self, "both_btn"):
            return
        key, enabled = start_button(self._quick_phase(), self.quick_mode)  # #1001
        enabled = enabled and not (self.same_lang and self._quick_phase() == "idle")  # #982
        if key == "gui_quick_both":
            key = "gui_start"  # #1028: the big Start says just "Start"; it still runs both legs
        self.both_btn.configure(text=t(key, s=self.load_shown), state="normal" if enabled else "disabled")
        if hasattr(self, "btn"):
            self.btn.configure(state="normal" if enabled or self._quick_phase() == "running" else "disabled")

    def _font_picked(self, _e=None):
        """Use the chosen system font for the subtitle log and the floating bar (saved)."""
        name = self.v_family.get()
        self.settings["font_family"] = "" if name == self._font_default else name
        save_settings(self.settings)
        self._chat_tags()
        try:
            self.overlay.set_font(self.settings["font_size"])
        except Exception:  # noqa: BLE001
            pass
        self.render_log()

    def _cpu_poll(self):
        """Whole-PC CPU load next to the log (Windows: GetSystemTimes; other systems: hidden). Never raises."""
        try:
            pct = _system_cpu_percent(self)
            if getattr(self, "_gpu_ok", None) is None:
                try:
                    from .core import _gpu_usable
                    self._gpu_ok = bool(_gpu_usable())
                except Exception:  # noqa: BLE001
                    self._gpu_ok = False
            on_gpu = self._gpu_ok and self.settings.get("gpu_use") != "off"
            mode = core.ui_text("gui_mode_gpu" if on_gpu else "gui_mode_cpu")
            gp = _nvml_gpu_percent() if self._gpu_ok else None
            if pct is not None:
                txt = mode + "  " + t("gui_cpu", p=pct) + ("" if gp is None else "  GPU %d%%" % gp)
                self.lbl_cpu.configure(text=txt, foreground=self.pal["err"] if max(pct, gp or 0) >= 85 else self.pal["muted"])
            elif not self.lbl_cpu.cget("text"):
                self.lbl_cpu.configure(text=mode)
            self.root.after(2000, self._cpu_poll)
        except Exception:
            pass

    def _build_log(self, f):
        """The conversation in the main window (#1028): chat bubbles, other side on the left, me on the right, the
        original text small under each bubble; scrolling history, newest at the bottom (#72)."""
        self.v_bar = tk.BooleanVar(value=self.settings["floating_bar"])
        bar = ttk.Frame(f)  # one dense toolbar: save / copy / topics at the left, font + A- A+ at the right
        bar.pack(side="top", fill="x")
        fb = ttk.Frame(f)
        self.font_box = fb
        ttk.Button(fb, text="A+", width=3, command=lambda: self.bump_log_font(2)).pack(side="right")
        ttk.Button(fb, text="A-", width=3, command=lambda: self.bump_log_font(-2)).pack(side="right", padx=(0, 2))
        try:  # every font the system has; "" = the program's default
            fams = sorted({f for f in tkfont.families(self.root) if not f.startswith("@")}, key=str.lower)
        except Exception:  # noqa: BLE001
            fams = []
        self._font_default = t("gui_font_default")
        self.v_family = tk.StringVar(value=self.settings.get("font_family") or self._font_default)
        fcb = ttk.Combobox(fb, textvariable=self.v_family, values=[self._font_default] + fams, width=14, state="readonly")
        fcb.pack(side="right", padx=(0, 6))
        fcb.bind("<<ComboboxSelected>>", self._font_picked)

        def _font_refresh(cb=fcb):  # re-read the system list each time the dropdown opens (new installs show up)
            try:
                fl = sorted({x for x in tkfont.families(self.root) if not x.startswith("@")}, key=str.lower)
                cb.configure(values=[self._font_default] + fl)
            except Exception:  # noqa: BLE001
                pass
        fcb.configure(postcommand=_font_refresh)
        # main-screen shortcuts: the things you need mid-meeting (they used to sit under Adjust > Advanced); their own
        # row under the font row, so the main window keeps its #1028 width in every UI language
        bar2 = bar
        ttk.Checkbutton(bar2, text=t("gui_save"), variable=self.v_save).pack(side="left")
        ttk.Button(bar2, text=t("gui_copy_all"), command=lambda: self._log_copy(True)).pack(side="left", padx=(6, 0))
        ttk.Button(bar2, text=t("gui_topics_menu"), command=self._topics_toggle).pack(side="left", padx=(6, 0))
        self.lbl_cpu = ttk.Label(bar2, text="", font="TkSmallCaptionFont")  # smaller: Linux fonts run wider (#1632)
        self.lbl_cpu.pack(side="left", padx=(6, 0))
        self._cpu_prev = None
        self._cpu_poll()
        box = tk.Frame(f)
        box.pack(side="top", fill="both", expand=True)
        self._font_inline = None

        def _font_place(e=None):  # wide: font controls share the toolbar row; narrow: their own row (no clipping)
            inline = f.winfo_width() >= FONT_INLINE_PX
            if inline == self._font_inline:
                return
            self._font_inline = inline
            fb.pack_forget()
            if inline:
                fb.pack(in_=bar, side="right")
            else:
                fb.pack(in_=f, side="top", anchor="e", before=box)
        f.bind("<Configure>", _font_place, add="+")
        self._log_holder, self._log_box = box, box
        sb = ttk.Scrollbar(box, orient="vertical")
        self.log_text = tx = tk.Text(box, bd=0, highlightthickness=0, wrap="word", padx=4, pady=6, cursor="arrow",
                                     takefocus=0, yscrollcommand=sb.set, width=1, height=1)
        sb.configure(command=tx.yview)
        sb.pack(side="right", fill="y")
        tx.pack(side="left", fill="both", expand=True)
        tx.configure(state="disabled")
        self.log_menu = tk.Menu(tx, tearoff=0)
        self.log_menu.add_command(label=t("gui_menu_wrong_line"), command=self.report_wrong_line)
        self.log_menu.add_command(label=t("gui_menu_copy"), command=self._log_copy)
        tx.bind("<Button-3>", self._log_context)
        # the log is a disabled Text that never takes focus -> Ctrl+C had nothing to act on; take focus on click
        tx.bind("<Button-1>", lambda e: tx.focus_set(), add="+")
        tx.bind("<Control-c>", lambda e: (self._log_copy(), "break")[1])
        tx.bind("<Control-a>", lambda e: (tx.tag_add("sel", "1.0", "end-1c"), "break")[1])
        tx.bind("<Configure>", self._log_resized)
        tx.bind("<Control-MouseWheel>", lambda e: self.bump_log_font(2 if e.delta > 0 else -2))
        tx.bind("<Control-Button-4>", lambda e: self.bump_log_font(2))
        tx.bind("<Control-Button-5>", lambda e: self.bump_log_font(-2))
        self.set_log_font(self.settings["log_font_size"])

    def _log_resized(self, e):
        """#1052: bubble width follows the window (75 %); only redraw when the width really changed."""
        if e.width != getattr(self, "_log_w", None):
            self._log_w = e.width
            self._chat_tags()
            if getattr(self, "_log_shown", None) and self._log_shown[0]:
                self.render_log()  # #1070: bubbles are wrapped to the new width

    def placeholder(self) -> list[tuple[str, bool]]:
        """What to show before the first subtitle of a session: the user must see that it is listening."""
        if self.listening:
            return [(t("gui_listening"), True)]
        tour = getattr(self, "tour", None)
        return [(tour.text()["step3"], True)] if tour is not None and tour.visible else []  # #314 step 3

    def set_log_font(self, n):
        self.settings["log_font_size"] = n = clamp_font(n, self.settings["log_font_size"])
        self._chat_tags(n)

    def _chat_tags(self, n: int | None = None):
        """Bubble look per theme (#1028): background colour on the text = the bubble; margins push it to a side."""
        n = n or self.settings["log_font_size"]
        pal = BUBBLE["light" if self.settings.get("theme") == "light" else "dark"]
        tx = self.log_text
        tx.configure(bg=pal["bg"])
        small = max(8, n * 2 // 3)
        fam = self.settings.get("font_family") or FONT_FAMILY  # user-picked system font, else the default
        # #1052: a bubble is at most ~75 % of the window and hugs its text: the background sits on the text only
        # (the line break is outside the bubble tag), margins push it to its side
        try:
            wide = max(0, int(tx.winfo_width() * 0.25))
        except Exception:  # noqa: BLE001
            wide = 80
        wide = max(wide, 40)
        for side, (lm, rm, just) in {"other": (10, wide, "left"), "me": (wide, 10, "right")}.items():
            bg, fg = pal[side]
            base = dict(lmargin1=lm, lmargin2=lm, rmargin=rm, justify=just)
            tx.tag_configure(f"{side}_base", **base)
            tx.tag_configure(f"{side}_head", foreground=pal["head"], font=(fam, small, "bold"), spacing1=8, **base)
            tx.tag_configure(f"{side}_final", background=bg, foreground=fg, font=(fam, n), spacing1=2,
                             spacing3=2, **base)
            tx.tag_configure(f"{side}_draft", background=bg, foreground=pal["sub"], font=(fam, n), spacing1=2,
                             spacing3=2, **base)
            tx.tag_configure(f"{side}_src", foreground=pal["sub"], font=(fam, small), **base)
        tx.tag_configure("hint", foreground=pal["sub"], font=(fam, small + 2), justify="center", spacing1=40)
        lbg, lfg = pal["latest"]  # #1644: laid over other_final on the newest line, so it must win
        tx.tag_configure("other_latest", background=lbg, foreground=lfg)
        tx.tag_raise("other_latest")
        self._topics_font(fam, n, small)

    def _topics_font(self, fam=None, n=None, small=None):
        """The key-points pane follows the subtitle font and size (family picker, log size)."""
        tt = getattr(self, "topics_text", None)
        try:
            if tt is None or not tt.winfo_exists():
                return
            n = n or self.settings["log_font_size"]
            fam = fam or self.settings.get("font_family") or FONT_FAMILY
            small = small or max(8, n * 2 // 3)
            tt.configure(font=(fam, n))
            tt.tag_configure("title", font=(fam, n + 1, "bold"))
            tt.tag_configure("when", font=(fam, small))
        except Exception:  # noqa: BLE001 - cosmetic
            pass

    def bump_log_font(self, d: int):
        follow = self._log_at_end()
        self.set_log_font(self.settings["log_font_size"] + d)
        if follow:
            self._log_to_end()
        return "break"  # Ctrl+wheel resizes; it must not also scroll

    def _log_at_end(self) -> bool:
        # yview fractions are estimates for wrapped lines; "the last line is on screen" is exact. #1384: flush the
        # pending layout first, else lines arriving faster than Tk idles see a stale view and stop following.
        self.log_text.update_idletasks()
        return self.log_text.dlineinfo("end-1c") is not None or follow_newest(self.log_text.yview()[1])

    def _log_to_end(self):
        # wrapped-line heights are only known after layout: scroll, flush the layout, scroll again (#1384). Not
        # after_idle: a pending callback would later undo a reader's scroll-up.
        self.log_text.yview_moveto(1.0)
        self.log_text.update_idletasks()
        self.log_text.yview_moveto(1.0)

    def render_log(self):
        """Redraw the history; keep following the newest line unless the user scrolled up to re-read."""
        tx = self.log_text
        top, follow = tx.index("@0,0"), self._log_at_end()
        hist = self.state.history()
        show_src = True  # #1052: the chat always shows the original small under each bubble (the bar keeps show_src)
        self._log_shown = (list(hist), show_src)  # what the clicks map onto (row index = the "row<i>" tag)
        tx.configure(state="normal")
        tx.delete("1.0", "end")
        if not hist:
            ph = self.placeholder()
            tx.insert("end", ph[0][0] if ph else self._empty_hint(), "hint")
        newest = max((i for i, (s, x, d) in enumerate(hist)  # #1644: newest finished line of the other side
                      if not d and not (x.startswith(SPEAK_MARK) or s.startswith(SPEAK_MARK))), default=-1)
        for i, (src, text, is_draft) in enumerate(hist):
            mine = text.startswith(SPEAK_MARK) or src.startswith(SPEAK_MARK)  # #1001: speak-leg lines are mine
            side = "me" if mine else "other"
            src, text = src.removeprefix(SPEAK_MARK), text.removeprefix(SPEAK_MARK)
            head = t("gui_chat_me" if mine else "gui_chat_other")
            lab = None if mine else self._speaker_label(i)
            if lab:  # 0.3.8: "[1] 說話者 1" (no 對方 word) - the name is a link: click to rename
                raw, shown = lab
                tag = f"spk:{raw}"
                color = self.spk_colors.color(raw)  # first-seen order, stable for the session
                av = f"spkav:{raw}"  # one tag per speaker, re-configured in place: no images, no widgets
                tx.insert("end", f" {speaker_initial(self.spk_book, raw)} ", (f"{side}_head", av))
                tx.insert("end", " ", f"{side}_head")
                tx.insert("end", shown, (f"{side}_head", tag))
                tx.insert("end", "\n", f"{side}_head")
                tx.tag_configure(av, background=color, foreground=SPK_AVATAR_FG)
                tx.tag_configure(tag, foreground=color)  # #1644: no underline; the hand cursor marks the link
                if tag not in self._spk_tags_bound:  # bind once per tag: every bind leaks a Tcl command
                    self._spk_tags_bound.add(tag)
                    tx.tag_bind(tag, "<Button-1>", lambda _e, r=raw: self._rename_speaker(r))
                    tx.tag_bind(tag, "<Enter>", lambda _e: tx.configure(cursor="hand2"))
                    tx.tag_bind(tag, "<Leave>", lambda _e: tx.configure(cursor=""))
            else:
                tx.insert("end", head + "\n", f"{side}_head")
            if show_src and src and src != text:  # #1605: source small first, translation large under it
                tx.insert("end", src + "\n", (f"{side}_src", f"row{i}"))
            self._insert_bubble(side, text, is_draft, i, latest=i == newest)
        tx.configure(state="disabled")
        if follow:
            self._log_to_end()
        else:
            tx.yview(top)

    def _empty_hint(self) -> str:
        """#1605: the empty subtitle area names the current source and target language."""
        from .. import langs

        src, dst = self._home_pair()
        return t("gui_chat_hint", src=langs.plain_label(src, ui_lang()), dst=choice_label("target", dst))

    def _speaker_label(self, row: int):
        """0.3.8: (raw label, text to show) of history row `row`, or None (no speaker ID / not ready / mine)."""
        who = self.state.history_who()
        if self.spk is None or row >= len(who) or not who[row]:
            return None
        raw = self.spk.result(who[row])
        clu = getattr(self, "spk_clusterer", None)
        if clu is not None:  # #1256: auto merges go into the book (Tk thread), which keeps a name given to either label
            for old, kept in clu.merge_edges():
                self.spk_book.merge(old, kept)
        raw = self.spk_book.resolve(raw) if raw else None
        shown = speaker_text(self.spk_book, raw, lambda n: t("gui_speaker", n=n)) if raw else None
        return (raw, shown) if shown else None

    def speaker_prefix(self, row: int):
        """Floating bar: (prefix text, color) of history row `row`, or None (labels off / not ready / mine)."""
        from .core import speaker_prefix

        lab = self._speaker_label(row)
        if not lab:
            return None
        return speaker_prefix(self.spk_book, self.spk_colors, lab[0], lambda n: t("gui_speaker", n=n))

    def _rename_speaker(self, raw: str):
        """0.3.8: click on a speaker label: ask for a name; it applies to every past and future bubble of that speaker
        in this session (SpeakerBook, never saved). An empty name goes back to "Speaker N"."""
        from tkinter import simpledialog

        cur = speaker_text(self.spk_book, raw, lambda n: t("gui_speaker", n=n)) or raw
        name = simpledialog.askstring(t("gui_chat_other"), t("gui_speaker_rename", name=cur), initialvalue=cur,
                                      parent=self.root)
        if name is None:
            return
        self.spk_book.rename(raw, name)
        self.render_log()
        if self.overlay.visible:
            self.overlay.render(self.state)  # the floating bar shows the new name at once

    def _close_labeler(self):
        """Stop the speaker-ID worker thread and let go of its model (Stop, restart, quit)."""
        lab = self.spk  # kept: the finished bubbles still read their labels from it
        if lab is not None:
            try:
                lab.close()
            except Exception:  # noqa: BLE001
                _diag.swallowed(__name__)

    def _make_labeler(self, mdir):
        """0.3.8 (worker thread): the async speaker-ID labeler for the listen leg, or None when sherpa-onnx / the
        speaker model is missing - then the GUI shows no labels and behaves exactly as before."""
        from . import speaker_pack

        from ..speaker_embed import LiveSpeakers

        lab = speaker_pack.make_labeler(self.settings, mdir, on_ready=lambda: self.q.put(("spk_ready",)),
                                        clusterer=self.spk_clusterer)
        self.spk = live = LiveSpeakers(lab)  # the pipeline reads it; the toggle can attach / detach mid-run
        return live

    def _spk_sync(self):
        """0.3.8: the toggle applies live. ON while a session runs: build the labeler off the Tk thread and attach it
        (new segments get labels from then on). OFF: detach and close it. Past bubbles keep what they have."""
        from ..models import models_dir
        from . import speaker_pack

        live = self.spk
        if live is None or live.closed or not hasattr(live, "set"):
            return  # no running session: the next Start reads the setting
        if not self.settings.get("speaker_id"):
            live.set(None)
            return
        if live.get() is not None:
            return
        mdir, settings, clusterer = models_dir(), self.settings, self.spk_clusterer

        def attach():
            try:
                lab = speaker_pack.make_labeler(settings, mdir, on_ready=lambda: self.q.put(("spk_ready",)),
                                                clusterer=clusterer)
            except Exception:  # noqa: BLE001
                _diag.swallowed(__name__)
                return
            if lab is None:
                return
            if settings.get("speaker_id") and live.get() is None:
                live.set(lab)
            else:  # switched off again while the model was loading
                lab.close()

        threading.Thread(target=attach, daemon=True, name="tlwu-speaker-attach").start()

    def _insert_bubble(self, side: str, text: str, is_draft: bool, row: int, latest: bool = False):
        """#1070: a bubble hugs its text (max ~75 % of the window). Tk paints the margin area with the background of the
        line's first character, so the text is wrapped here and every line starts with a plain spacer (no
        background) that carries the margin; the background tag covers only the words."""
        import tkinter.font as tkfont

        from .core import wrap_bubble

        tx = self.log_text
        n = self.settings["log_font_size"]
        width = tx.winfo_width()
        width = width if width > 100 else 400
        try:
            fnt = tkfont.Font(family=FONT_FAMILY, size=n)
            measure = fnt.measure
        except Exception:  # noqa: BLE001
            measure = lambda s: len(s) * n  # noqa: E731
        lines = wrap_bubble(text, measure, max(60, int(width * 0.75) - 24))
        top = max(measure(" " + ln + " ") for ln in lines)
        margin = 10 if side == "other" else max(10, (width - 8 - 10 - top) // 4 * 4)
        mtag = f"{side}_m{margin}"
        tx.tag_configure(mtag, lmargin1=margin, lmargin2=margin, rmargin=0, justify="left")
        fin = f"{side}_draft" if is_draft else f"{side}_final"
        words = (fin, "other_latest", f"row{row}") if latest else (fin, f"row{row}")
        for ln in lines:
            tx.insert("end", " ", (mtag, f"row{row}"))
            tx.insert("end", f" {ln} ", words)
            tx.insert("end", chr(10), (mtag, f"row{row}"))

    def _sync_bar(self):
        """#1042: the floating-subtitles button always shows whether the bar is really on screen."""
        self.v_bar.set(bool(self.overlay.visible))

    def _bar_toggled(self):
        want = not self.overlay.visible
        self.v_bar.set(want)  # one click always toggles what is actually shown
        self.settings["floating_bar"] = want
        if want:  # #1028: the "floating subtitles" button shows the bar at once, also before Start
            self.overlay.show()
            self.overlay.render(self.state)
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
        """#739: picking a model downloads nothing; a missing one downloads on the first Start (_offer_model)."""
        self._asr_hint()  # #1385: Manual: the hint names the picked model and its download size

    def _offer_model(self, run: dict, mode) -> bool:
        """#739: True when the chosen speech model is missing: its download starts now with a progress bar and this
        Start resumes by itself when it succeeds (no dialog, no second click)."""
        # #1385: only Accurate names a model that may not be on disk yet; Fast / Auto use what is installed (the
        # CLI's auto_model), so they never start a download here (a fresh setup with no model keeps its old error)
        want = self._speech_model("quality") if run.get("quality") == "quality" else None
        m = missing_start_model(run, want=want)  # #1385: Accurate downloads the larger model it names
        bg = getattr(self, "bg_dl", None)
        if m is not None and bg is not None and bg.active:  # #1358: no second manifest writer while bg runs
            return True
        if m is None or self.downloading:
            return self.downloading is not None and m is not None
        self.downloading = m
        self.pack_retry = mode
        self.start_dl = StartDownload(m)
        self.v_dl_pct.set(0)
        self.dl_bar.pack(anchor="w", pady=(4, 0))
        self.btn.configure(state="disabled")
        self._status_text(t("gui_dl_running", model=m))
        self._sync_quick()
        threading.Thread(target=run_download, args=(m, self.q.put), daemon=True).start()
        return True

    def _dl_event(self, ev: tuple):
        """#739: feed a download event to the progress bar (the line text stays the status text)."""
        if self.start_dl is None:
            return
        self.start_dl.feed(ev)
        self.v_dl_pct.set(self.start_dl.percent)

    def _offer_pack(self, run: dict, mode) -> bool:
        """#673: True when a pack is missing (dialog shown / download running) and this Start must not go on now.
        "Not now" changes nothing (mode and language stay as they were)."""
        from ..models import models_dir

        try:
            need = pack_on_demand.first_missing(run.get("lang"), run.get("target"), models_dir())
        except Exception:  # noqa: BLE001  # no setup package / unreadable manifest: behave as before
            return False
        bg = getattr(self, "bg_dl", None)
        if need is not None and bg is not None and bg.active and pack_on_demand.only_translation_model(need[0]):
            return False  # #1375: only Hy-MT2 is downloading: listen starts with --mt-pending (source-text lines)
        if need is not None and bg is not None and (bg.active or bg.errors):  # #1358: one setup child at a time
            if not bg.active:
                bg.errors.clear()
                bg.start()  # Start after a failed background download is the retry
            return True  # an installed configuration (need is None) is never blocked by the background download
        if need is None or self.downloading:
            return self.downloading is not None and need is not None
        pack, mb = need
        if not messagebox.askyesno(t("gui_dl_title"), pack_on_demand.dialog_text(ui_lang(), pack, mb), parent=self.root):
            return True
        self.downloading = pack
        self.pack_retry = mode
        self.start_dl = StartDownload(pack)  # #739: packs show the same progress bar as models
        self.v_dl_pct.set(0)
        self.dl_bar.pack(anchor="w", pady=(4, 0))
        self.btn.configure(state="disabled")
        self._status_text(t("gui_dl_running", model=pack))
        self._sync_quick()  # #1028: the progress line is in the main window; the Settings panel stays closed
        threading.Thread(target=run_pack_download, args=(pack_on_demand.real_pack(pack), self.q.put), daemon=True).start()
        return True

    def _offer_voice(self, run: dict, mode: str = "speak", resume: bool = True) -> bool:
        """#967: True when this speak Start must not go on now: the Kokoro voice is missing and the question was
        answered "no" (speak runs subtitles only, as before) or the install is running (Start resumes in _pack_done)."""
        from ..models import models_dir
        from . import voice_install
        from .core import run_voice_install

        try:
            if self.downloading or not voice_install.offer_needed(run, models_dir()):
                return False
        except Exception:  # noqa: BLE001  # an unreadable path must not block Start
            return False
        lang = ui_lang()
        ru = voice_install.is_ru(run)  # Russian: Piper voice + piper-tts into the TTS venv, no Kokoro
        name = voice_install.pack_name(run)
        if not messagebox.askyesno(voice_install.title_text(lang), voice_install.dialog_text(lang, ru), parent=self.root):
            if ru and resume and getattr(self, "ru_notice", None) is not None:  # Start fallback: same lasting notice
                self.ru_notice_lbl.configure(text=voice_install.notice_text(lang))
                self.ru_notice_btn.configure(text=voice_install.notice_button(lang))
                self.ru_notice.grid()
            return False  # "no": speak starts with subtitles only, the red line explains why there is no voice
        self.downloading = name
        self.pack_retry = mode
        self.pack_resume = resume  # False: asked from the language picker, nothing starts when the download ends
        self.start_dl = StartDownload(name)
        self.v_dl_pct.set(0)
        self.dl_bar.pack(anchor="w", pady=(4, 0))
        self.btn.configure(state="disabled")
        self._status_text(t("gui_dl_running", model=name))
        self._sync_quick()
        threading.Thread(target=run_voice_install, args=(self.q.put,), kwargs={"ru": ru}, daemon=True).start()
        return True

    def _offer_cable(self, mode: str = "speak") -> bool:
        """#804: True when this speak Start must not go on now: the VB-CABLE question was answered "no" (back to
        listen) or the download + elevated install is running (Start resumes in _cable_done).
        #1001 (mode both): "no" lets the Start go on (listening + speak subtitles only), so False."""
        from .. import vbcable

        if self.downloading or not vbcable.needs_offer(vbcable.output_names()):
            return False
        lang = ui_lang()
        if not messagebox.askyesno(vbcable.title_text(lang), vbcable.dialog_text(lang), parent=self.root):
            return mode != BOTH_MODE  # both: go on with listening + speak subtitles
        self.cable_retry = mode
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
            self.toggle(getattr(self, "cable_retry", None) or "speak")
            return
        if res == "page":
            import webbrowser

            self._status_text("")  # #804 review: drop the "Downloading VB-CABLE..." line

            webbrowser.open(vbcable.VBCABLE_PAGE)
            return
        self._status_text(vbcable.fail_text(lang))
        messagebox.showwarning(vbcable.title_text(lang), vbcable.fail_text(lang), parent=self.root)

    def _pack_done(self, pack: str, rc: int):
        if pack == "topics":  # #1379
            return self._topics_done(rc)
        if pack == "speaker":
            self._dl_event(("pack_done", pack, rc))
            return self._spk_done(rc)
        self._dl_event(("pack_done", pack, rc))
        shown = pack_on_demand.shown_pack(self.downloading, pack)  # #1181: same name as the dialog ("hymt2"), not "ko"
        self.start_dl = None
        self.dl_bar.pack_forget()
        self.downloading = None
        self.btn.configure(state="normal")
        self._status_text(t("gui_dl_ok" if rc == 0 else "gui_dl_fail", model=shown))  # #1042: error shows in the main window
        self._sync_quick()
        if getattr(self, "ru_notice", None) is not None:
            self.root.after(100, self._ru_voice_check)  # hides the notice once the Russian voice is there
        if rc == 0 and getattr(self, "pack_resume", True):  # on without restarting the app
            self.toggle(getattr(self, "pack_retry", None))
        self.pack_resume = True

    def _cpu_ram(self):
        """#780: RAM (GB) for the "too heavy" model marks on a PC without a CUDA device, else None."""
        try:
            from ..device import resolve_device

            # same decision as session setup (GPU present but CUDA libs missing -> cpu), no warnings from the probe
            return cpu_ram_gb(resolve_device("auto", warn=lambda m: None) == "cuda")
        except Exception:  # noqa: BLE001 - a failed probe must not break the window
            return None

    def _download_done(self, model: str, rc: int):
        self._dl_event(("dl_done", model, rc))
        sd, self.start_dl = self.start_dl, None
        self.dl_bar.pack_forget()
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
        if sd is not None and ok:  # #739: the first Start goes on by itself
            self.toggle(getattr(self, "pack_retry", None))

    def _topics_copy(self):
        """Copy the selected meeting-notes text, or all of it when nothing is selected."""
        tx = self.topics_text
        try:
            txt = tx.get("sel.first", "sel.last")
        except tk.TclError:
            txt = tx.get("1.0", "end-1c")
        if txt:
            self.root.clipboard_clear()
            self.root.clipboard_append(txt)

    def render_topics(self, show: bool = False):
        """#611: the topic notes side window: one block per finished topic (title, time, key points, actions), plus
        the on/off switch and the GGUF model picker. Created on first use; redrawn from self.topic_panel."""
        w = self.topics_win
        if w is not None and w.winfo_exists() and isinstance(w, _FloatPane) != notes_float_on(self.settings):
            w.withdraw()  # #1705: the mode changed: rebuild the pane in the other host (the model is the same)
            w.destroy()
            w = self.topics_win = None
        if w is None or not w.winfo_exists():
            if not show:
                return
            holder = self._log_holder
            inner = [c for c in holder.winfo_children() if isinstance(c, tk.Text) or isinstance(c, ttk.Scrollbar)]
            w = self.topics_win = (_FloatPane(self) if notes_float_on(self.settings)
                                   else _DockPane(self, holder, before=inner[0] if inner else None))
            ttk.Label(w, text=t("gui_topics_title"), font=("TkDefaultFont", 11, "bold")).pack(side="top", anchor="w", padx=8, pady=(4, 0))
            bar = ttk.Frame(w)
            bar.pack(side="top", fill="x", padx=8, pady=6)
            self._sync_topics_var()
            ttk.Checkbutton(bar, text=t("gui_topics_switch"), variable=self._topics_var(),
                            command=self._topics_toggled).pack(side="left")
            ttk.Button(bar, text="✕", width=3, command=self._topics_close).pack(side="right")
            float_now = notes_float_on(self.settings)  # #1705: pop out into a floating window / dock back
            ttk.Button(bar, text=t("gui_topics_dock" if float_now else "gui_topics_popout"),
                       command=self._notes_dock if float_now else self._notes_popout).pack(side="right", padx=(0, 6))
            ttk.Button(bar, text=t("gui_topics_model"), command=self.pick_topics_model).pack(side="right", padx=(0, 6))
            ttk.Button(bar, text=t("gui_log_copy"), command=self._topics_copy).pack(side="right", padx=(0, 6))
            self.topics_cancel = ttk.Button(bar, text=t("gui_topics_cancel"), command=self._cancel_topics_download)
            self.topics_lang = ttk.Label(w, wraplength=DOCK_W - 30, justify="left")  # #1379: which language the notes use
            self.topics_lang.pack(side="top", fill="x", padx=8)
            self.topics_hint = self._tok(ttk.Label(w, wraplength=DOCK_W - 30, justify="left"), "muted")
            self.topics_hint.pack(side="top", fill="x", padx=8)
            self.topics_busy = self._tok(ttk.Label(w), "accent")  # the model can take a minute: show it is working
            self.topics_busy.pack(side="top", fill="x", padx=8)
            self.topics_text = tk.Text(w, wrap="word", height=20, borderwidth=0, padx=8, pady=6)
            self.topics_text.pack(side="top", fill="both", expand=True)
            self._topics_font()
            self.topics_text.bind("<Button-1>", lambda e: self.topics_text.focus_set(), add="+")
            self.topics_text.bind("<Control-c>", lambda e: (self._topics_copy(), "break")[1])
        self._notes_tags()
        from ..langs import name as lang_name
        from .core import TOPICS_MB, _gpu_usable
        from .topics_panel import panel_hint

        entries = self.topic_panel.entries()
        downloading = self.downloading == "topics"
        key = panel_hint(downloading=downloading, has_model=bool(topics_model_path(self.settings)),
                         has_entries=bool(entries), choice=self.settings.get("topics"), has_cuda=_gpu_usable)
        self.topics_hint.configure(text=t(key, mb=TOPICS_MB) if key else "")
        self.topics_lang.configure(text=t("gui_topics_lang", lang=lang_name(self.settings.get("target") or "zh-tw")))
        if downloading:
            self.topics_cancel.pack(side="right", padx=(0, 6))
        else:
            self.topics_cancel.pack_forget()
        tx = self.topics_text
        tx.configure(state="normal")
        tx.delete("1.0", "end")
        opened = self.__dict__.setdefault("_topics_open", set())
        tx.tag_configure("bullet", lmargin1=8, lmargin2=22)  # hanging indent: the dot and its text share a line
        for i, e in enumerate(entries):
            tx.insert("end", ("\u25be " if i in opened else "\u25b8 ") + e["title"], ("title", f"t{i}"))
            tx.insert("end", "  " + e["when"] + "\n", "when")
            for b in e["bullets"]:
                tx.insert("end", "\u2022", ("mark", "bullet", f"b{i}"))
                tx.insert("end", f" {b}\n", ("bullet", f"b{i}"))
            tx.tag_configure(f"b{i}", elide=i not in opened)  # collapsed: titles only; click a title to read its points
            tx.tag_bind(f"t{i}", "<Button-1>", lambda _e, i=i: self._topic_toggle(i))
            tx.tag_bind(f"t{i}", "<Enter>", lambda _e: tx.configure(cursor="hand2"))
            tx.tag_bind(f"t{i}", "<Leave>", lambda _e: tx.configure(cursor=""))
        tx.configure(state="disabled")
        self._topics_busy_tick()
        if show and not self._topics_closed:
            w.deiconify()
            w.lift()

    _topics_busy_job = None

    def _topic_toggle(self, i: int):
        """Click a topic title: show / hide its key points (the set of open topics is kept across redraws)."""
        self.__dict__.setdefault("_topics_open", set()).symmetric_difference_update({i})
        self.render_topics()

    def _topics_busy_tick(self):
        """Label 'Summarizing... N s' while the model runs a topic; refreshes every second, leaves the text alone."""
        if self._topics_busy_job is not None:
            try:
                self.root.after_cancel(self._topics_busy_job)
            except Exception:  # noqa: BLE001
                pass
            self._topics_busy_job = None
        w = self.topics_win
        if w is None or not w.winfo_exists():
            return
        secs = self.topic_panel.busy_for()
        self.topics_busy.configure(text="" if secs is None else t("gui_topics_busy", s=int(secs)))
        if secs is not None:
            self._topics_busy_job = self.root.after(1000, self._topics_busy_tick)

    _topics_closed = False

    def _set_notes_float(self, on: bool):
        """#1705: move the notes between the side panel and the floating window; the choice is saved."""
        self.settings["notes_float"] = bool(on)
        save_settings(self.settings)
        if getattr(self, "v_notes_float", None) is not None:
            self.v_notes_float.set(bool(on))
        self._topics_closed = False
        self.render_topics(show=True)

    def _notes_popout(self):
        self._set_notes_float(True)

    def _notes_dock(self):
        self._set_notes_float(False)

    def _notes_float_toggled(self):
        self._set_notes_float(bool(self.v_notes_float.get()))

    def _topics_close(self):
        self._topics_closed = True
        if self.topics_win is not None:
            self.topics_win.withdraw()

    def _topics_toggle(self):
        """The toolbar button: show the pane, or hide it when it is already there."""
        w = self.topics_win
        if w is not None and w.winfo_exists() and w.winfo_manager():
            self._topics_close()
        else:
            self._topics_closed = False
            self.render_topics(show=True)

    def _notes_tags(self):
        """#1644: the notes text follows the app theme; time range muted, bullet marks in the notes accent."""
        pal = NOTES["light" if self.settings.get("theme") == "light" else "dark"]
        tx = self.topics_text
        tx.configure(bg=pal["bg"], fg=pal["fg"], insertbackground=pal["fg"])
        tx.tag_configure("title", foreground=pal["title"])
        tx.tag_configure("when", foreground=pal["when"])
        tx.tag_configure("mark", foreground=pal["mark"])

    def _topics_var(self):
        """#1379: one BooleanVar for the Settings menu switch and the panel's switch."""
        if getattr(self, "v_topics", None) is None:
            self.v_topics = tk.BooleanVar(master=self.root, value=self.settings.get("topics") is True)
        return self.v_topics

    def _sync_topics_var(self):
        self._topics_var().set(self.settings.get("topics") is True or topics_on(self.settings))

    def _topics_toggled(self):
        on = bool(self._topics_var().get())
        self.settings["topics"] = on
        save_settings(self.settings)
        if on and not topics_model_path(self.settings) and not self._offer_topics_model():
            self.settings["topics"] = False  # "Not now" (or another download running): the switch stays off
            save_settings(self.settings)
            self.v_topics.set(False)
        self.render_topics(show=on)

    def _offer_topics_model(self) -> bool:
        """#1379: first switch-on without a model: download the #1378 default through the same pack download child
        (progress bar in the main window, resumable, SHA256-pinned). True = the download runs."""
        from .core import TOPICS_MB, topics_download_cmd

        if self.downloading:
            return False
        name = t("gui_topics_switch")
        if not messagebox.askyesno(t("gui_dl_title"), pack_on_demand.dialog_text(ui_lang(), name, TOPICS_MB),
                                   parent=self.root):
            return False
        self.downloading = "topics"
        self.topics_proc = None
        self.start_dl = StartDownload(name)
        self.v_dl_pct.set(0)
        self.dl_bar.pack(anchor="w", pady=(4, 0))
        self._status_text(t("gui_topics_downloading", mb=TOPICS_MB))
        self._sync_quick()
        threading.Thread(target=run_pack_download, args=("topics", self.q.put),
                         kwargs={"cmd": topics_download_cmd(), "on_start": self._topics_started}, daemon=True).start()
        return True

    def _topics_started(self, proc):  # download worker thread
        self.topics_proc = proc

    def _cancel_topics_download(self):
        proc = getattr(self, "topics_proc", None)
        if proc is not None and proc.poll() is None:
            proc.terminate()  # the child ends with rc != 0 -> _topics_done turns the switch back off

    def _topics_done(self, rc: int):
        """#1379: the notes model download ended: on success the switch stays on, else it goes back off."""
        self._dl_event(("pack_done", "topics", rc))
        self.start_dl, self.downloading, self.topics_proc = None, None, None
        self.dl_bar.pack_forget()
        ok = rc == 0 and bool(topics_model_path(self.settings))
        if not ok:
            self.settings["topics"] = False
            save_settings(self.settings)
            self._topics_var().set(False)
        self._status_text(t("gui_dl_ok" if ok else "gui_dl_fail", model=t("gui_topics_switch")))
        self._sync_quick()
        self.render_topics(show=True)

    def pick_topics_model(self):
        p = filedialog.askopenfilename(title=t("gui_topics_model"), filetypes=[("GGUF", "*.gguf"), ("All", "*.*")])
        if p:
            self.settings["topics_model"] = p
            save_settings(self.settings)
            self.render_topics()

    def pick_save(self):
        p = filedialog.asksaveasfilename(title=t("gui_pick_save"), defaultextension=".jsonl",
                                         filetypes=[("JSON lines", "*.jsonl"), ("All", "*.*")])
        if p:
            self.v_save_path.set(p)
            self.v_save.set(True)

    def _tech_toggled(self):
        """#1806: the "Show technical details" switch: only the label texts change, never a stored value."""
        self.settings["tech_labels"] = self.v_tech.get()
        core.set_tech_labels(setting=self.v_tech.get())
        self.relabel()  # #1807: labels and status lines follow the switch at once
        self._relabel_combos()

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
        """The GPU / package probes in env_info() take seconds: run them (and the browser launch) off the GUI
        thread so the window never freezes; a "no browser" URL comes back through the queue."""
        from ..report import open_report
        from .core import report_info

        info = report_info(self.settings)

        def work():
            try:
                open_report(info=info, say=lambda m: self.q.put(("status", t("gui_open_url", url=m[:80]))))
            except Exception:  # noqa: BLE001 - a failed report must not kill the thread silently
                _diag.swallowed(__name__)

        threading.Thread(target=work, daemon=True).start()

    def copy_ai_prompt(self):
        """#940: prompt + report_info + last 50 redacted log lines to the clipboard; nothing is sent anywhere."""
        from ..report import build_ai_text, env_info
        from .core import report_info

        extra = report_info(self.settings)

        def work():  # slow probes off the GUI thread; the clipboard is set by poll() on the GUI thread
            try:
                info = dict(env_info())
                info.update(extra)
                self.q.put(("clip_ai", build_ai_text(info)))
            except Exception:  # noqa: BLE001
                _diag.swallowed(__name__)

        threading.Thread(target=work, daemon=True).start()

    def collect(self):
        s = self.settings
        self._remember_panel()
        s.update(lang=lang_from_label(self.v_lang.get()), target=self.v_target.get(), engine=engine_from_label(self.v_engine.get()), quality=self.v_quality.get(),
                 model=model_from_label(self.v_model.get()), preset=self.v_preset.get(), glossary=self.v_gloss.get().strip(), save=self.v_save.get(),
                 save_path=self.v_save_path.get().strip(), show_src=self.var_show_src.get(),
                 tech_labels=self.v_tech.get(), live_draft=self.v_live.get(), source=self.v_source.get(), floating_bar=self.v_bar.get(),
                 listen_device="" if self.v_listen_dev.get() == self.listen_default else self.v_listen_dev.get(),
                 mic_device="" if self.v_mic_dev.get() == self.mic_default else self.v_mic_dev.get(),
                 gpu_use=self.v_gpu_use.get(),
                 voice=self.v_voice.get(), send_mode=self.v_send_mode.get(),
                 voice_device="" if self.v_voice_dev.get() == self.voice_default else self.v_voice_dev.get())
        save_settings(s)

    # -- worker -------------------------------------------------------------------------------
    def _wait_line(self) -> str:
        """#1259 / #1253: the ASR backlog (the slower leg) and the translations held for ASR (all legs), or ""."""
        from ..cli import status_wait_text

        waits = getattr(self, "_waits", {})
        return status_wait_text(max([n for (k, _), n in waits.items() if k == "backlog"] or [0]),
                                sum(n for (k, _), n in waits.items() if k == "mtwait"))

    def toggle(self, mode: str | None = None):
        """Start / Stop. `mode`: None or BOTH_MODE = the one Start (#1001: listen + speak together); "listen" /
        "speak" = one leg only (kept for tests and the #983 restart of an older session)."""
        if self.worker is not None and self.worker.is_alive():
            self.stop_ev.set()
            self.btn.configure(state="disabled")
            self.set_status("gui_stopping")
            self._sync_quick()
            return
        if self.settings["floating_bar"] and not self.overlay.visible:
            self.v_bar.set(True)  # #1042: the saved choice is shown again at Start (Stop/launch left the button off)
        self.collect()
        mode = mode or scene_mode(self.settings)  # 0.3.8: the 情境 row decides what the one Start starts
        speak_run = None
        try:
            if mode in (BOTH_MODE, WATCH_MODE):  # #1001: listen + speak; 0.3.8: video = listen only
                _m, run, speak_run = scene_runs(dict(self.settings, scene="video" if mode == WATCH_MODE else "meeting"))
            else:
                run = quick_settings(self.settings, mode)
        except SameLanguageError as e:  # #981: same language on both sides: say so, no run starts
            self.set_status(e.key)  # #1028: the status line is in the main window
            return
        speaking = mode in ("speak", BOTH_MODE)
        voice_run = speak_run if speak_run is not None else run  # the run that speaks into the meeting
        if speaking and self._offer_cable(mode):  # #804: no virtual mic -> offer the VB-CABLE install first
            return
        if self._offer_model(run, mode):  # #739: a missing speech model downloads here with a progress bar
            return
        if speaking and self._offer_voice(voice_run, mode):  # #967: no Kokoro voice -> offer the install, Start resumes
            return
        if self._offer_pack(run, mode) or (speak_run is not None and self._offer_pack(speak_run, mode)):  # #673
            return
        self.quick_mode, self.run_source = mode, run.get("source")
        self._sync_scene()
        self._last_mode = mode or "listen"  # the finished rows keep this mode after Stop (#677)
        self.speak_hint = speak_route(voice_run, meeting_cable()) if speaking else None  # #710
        self.gate = None
        if speaking and voice_run.get("voice"):  # #833: each speak line is held and sent by the #832 state machine
            from ..reverse.sendgate import SendGate

            self.gate = SendGate(lambda _t: None, time.monotonic, mode=run.get("send_mode", "confirm"))
        self.state = SubtitleState(HISTORY_LINES)
        from ..speakers import SpeakerBook

        self._close_labeler()  # the previous session's worker thread and model
        from ..speakers import SpeakerClusterer

        self.spk, self.spk_book = None, SpeakerBook()  # new session: new speakers, new names
        self.spk_clusterer = SpeakerClusterer()  # ONE per session: off -> on keeps numbering, names and colors
        self.spk_colors = SpeakerColors()
        self.set_paused(False)
        self.stop_ev = threading.Event()
        self.load_t0 = time.monotonic()  # #332: Stop stays disabled ("Loading... N s") until the models are ready
        self._slow_clear()
        from .core import SLOW_LOAD_SECS

        self._slow_after = self.root.after(int(SLOW_LOAD_SECS * 1000), self._slow_load_due)
        self.load_shown = 0
        self.btn.configure(text=t("gui_loading", s=0), state="disabled")
        self.set_status("gui_starting")
        self.set_dot("loading")
        notice_key, show_panel = speak_start_view(self.speak_hint)  # #710: the Speak lines live in the Adjust panel
        if mode == BOTH_MODE:  # #1001: the panel opens only for the "no CABLE" line, which says listening still runs
            notice_key, show_panel = ("gui_both_no_voice", True) if notice_key else ("", False)
        self.v_notice.set(t(notice_key) if notice_key else "")
        if self.mic_off and mode != WATCH_MODE:  # #1028: the mic toggle was left off (no mic in 看影片); keep an existing notice (no CABLE) above it
            self.v_notice.set(t("gui_mic_off_note") if not notice_key else f"{t(notice_key)}\n{t('gui_mic_off_note')}")
        if show_panel:
            self._show_panel()
        self.errored = False
        self.listening = True
        self.speak_run = speak_run  # #1001: read by _work (both legs) before the worker posts "stopped"
        self.__dict__["_topics_open"] = set()  # topic numbers restart with the run
        self.topic_panel.clear()  # #611: the panel shows this run's topics only (the .topics.md file keeps them)
        if topics_on(self.settings) and topics_model_path(self.settings) and not self._topics_closed:
            self.render_topics(show=True)  # notes are on: the pane is there from the start of the meeting
        self.worker = threading.Thread(target=self._work, args=(run, self.stop_ev), daemon=True)
        self.worker.start()
        self.run_snapshot = self._start_values()  # #983
        self.run_pair = self._home_pair()  # #1020
        self._restart_check()
        self._sync_quick()
        self.render_log()  # shows "Listening..." at once, before the first line
        if self.settings["floating_bar"]:
            self.overlay.show()
            self.overlay.render(self.state)

    def _work(self, s: dict, stop: threading.Event):
        """Worker thread: no tkinter here, only self.q. `s` = the listen leg (or the one leg); self.speak_run, when
        set, is the speak leg that runs next to it (#1001)."""
        speak = getattr(self, "speak_run", None)
        if speak is not None:
            self._work_both(s, speak, stop)
            return
        try:
            self._run_leg(s, stop, self.gate)  # #833: gate set before this thread starts, cleared after "stopped"
        finally:
            self.q.put(("stopped",))

    def _work_both(self, listen: dict, speak: dict, stop: threading.Event):
        """#1001 worker: the listen and the speak leg each in its own thread with the same stop event; "stopped" is
        posted once, after both have ended (no leg outlives Stop). One leg failing leaves the other running.
        TLWU_BOTH=1 while it runs, so the #250 CPU thread budget is split between the two legs."""
        gate = self.gate
        old = os.environ.get("TLWU_BOTH")
        os.environ["TLWU_BOTH"] = "1"
        try:
            legs = [threading.Thread(target=self._run_leg, args=(listen, stop, None), daemon=True, name="tlwu-listen"),
                    threading.Thread(target=self._run_leg, args=(speak, stop, gate, SpeakQueueSink), daemon=True,
                                     name="tlwu-speak")]
            for th in legs:
                th.start()
            for th in legs:
                th.join()
        finally:
            if old is None:
                os.environ.pop("TLWU_BOTH", None)
            else:
                os.environ["TLWU_BOTH"] = old
            self.q.put(("stopped",))

    def _run_leg(self, s: dict, stop: threading.Event, gate=None, sink_cls=None):
        """One run_session on this thread; an error goes to self.q (localized), never raised. No tkinter here."""
        q = self.q
        leg = "speak" if sink_cls is not None else "listen"  # #1253: backlog / held-MT counts are kept per leg
        try:
            from ..cli import parse_args, run_session
            from ..stream import DraftEngine
            from .core import engine_model_missing

            missing = engine_model_missing(s)  # #1351: opt-in 7B picked but not downloaded: say so, do not start
            if missing:
                q.put(("error", missing))
                return

            a = parse_args(settings_to_argv(s, outputs=voice_outputs() if s.get("voice") else None,
                                            notice=lambda m: q.put(("notice", m))))
            a.voice_gate = gate  # #833: open_voice routes every spoken line through it
            if sink_cls is not None:  # the speak leg: the mic toggle mutes its capture (#1028)
                a.mic_mute = self.mic_ev

            def draft_factory(asr, translator, segmenter, tgt):
                if not s["live_draft"] or translator is None:
                    return None
                return DraftEngine(asr, translator, QueueDraftRenderer(q), lambda: segmenter.live, tgt)

            sink = (sink_cls or QueueSink)(q)
            if sink_cls is not None:
                sink.variant = "cn" if s.get("mine") == "zh-cn" else "tw"  # #1070
            run_session(a, sink=sink, draft_factory=draft_factory, stop=stop,
                        status=lambda m: q.put(("status", m)),
                        notice=lambda m: q.put(("notice", m)),
                        cpu_line=lambda ms, path=None: q.put(("cpu_line", ms, path)),
                        backlog_line=lambda n: q.put(("backlog", n, leg)),  # #1259
                        mt_wait_line=lambda n: q.put(("mtwait", n, leg)),  # #1253; per leg, both legs may hold
                        labeler_factory=self._make_labeler if sink_cls is None and s.get("source") == "system" else None,
                        on_topic=self.topic_panel if sink_cls is None else None)  # #611
        except (Exception, SystemExit) as e:  # noqa: BLE001  # #1351: a CLI SystemExit must not stop the GUI silently
            print(f"tlwu: {type(e).__name__}: {e}", file=sys.stderr, flush=True)  # raw text for the log
            q.put(("error", gui_error_text(e)))  # #885: localized, no CLI flags

    def _bg_defaults_start(self):
        """#1358: one `--default-listen` child per missing default model per run; progress comes back through the queue."""
        from ..models import models_dir
        from . import bg_download
        from .core import default_listen_cmd

        try:
            mdir = models_dir()
            if self.downloading:  # a foreground setup child is running: do not start a second manifest writer
                self.root.after(5000, self._bg_defaults_start)
                return
            if not hasattr(self, "bg_dl"):
                self.bg_dl = bg_download.BackgroundDownloads(
                    default_listen_cmd(mdir), lambda x: self.q.put(("bg_dl_status", x)),
                    lambda k: self.q.put(("bg_dl_ready", k)), mdir)
            self.bg_dl.start()
        except Exception as e:  # noqa: BLE001  # no setup package: the first-Start dialog still works
            print(f"tlwu: background download not started: {type(e).__name__}: {e}", file=sys.stderr, flush=True)

    def poll(self):
        if self.closing:
            return
        self._gate_tick()
        if self.sigint:
            self.quit()
            return
        changed = False
        try:
            while True:
                ev = self.q.get_nowait()
                kind = ev[0]
                if kind == "bg_dl_status":  # #1358
                    if not self.downloading and self.load_t0 is None:
                        self._status_text(ev[1])
                elif kind == "bg_dl_ready":  # #1358: hand-over hook of the pending translator (sibling issue)
                    self.bg_ready = getattr(self, "bg_ready", set()) | {ev[1]}
                elif kind == "status":
                    from ..cli import READY_STATUS  # already imported by the worker; not at GUI start (#332)

                    st = load_status_text(ev[1])  # #1020: in the UI language
                    if self._slow_shown and self.load_t0 is not None and not ev[1].startswith(READY_STATUS):
                        from .core import slow_load_line

                        st = f"{st}\n{slow_load_line(float('inf'))}"  # still loading: keep the slow line
                    self._status_text(st)

                    if ev[1].startswith(READY_STATUS) and self.load_t0 is not None:
                        self.load_t0 = None
                        self._slow_clear()  # ready: the slow-load line is gone for good
                        self.btn.configure(text=t("gui_stop"), state="normal")
                        self.set_dot("on")
                        ready = "gui_ready_speak" if self.run_source == "mic" else "gui_ready_listen"
                        if self.speak_hint == SPEAK_HINT_CABLE:  # #710: voice goes into CABLE Input
                            ready = self.speak_hint
                        text = t(ready)
                        if self.quick_mode == BOTH_MODE:  # #1001: both legs; keep the #710 CABLE hint after it
                            text = t("gui_ready_both") + (f" | {text}" if ready == SPEAK_HINT_CABLE else "")
                        import logging

                        logging.getLogger("tlwu.gui").info("%s", load_status_text(ev[1]))  # breakdown: log only
                        self._status_text(text)  # #294
                        self._sync_quick()
                elif kind in ("backlog", "mtwait"):  # #1259: "ASR n s behind" in the status line, the old text back
                    # when caught up; #1253: plus translations held for ASR. Kept per leg (listen / speak).
                    if getattr(self, "_waits", None) is None:
                        self._waits = {}
                    self._waits[(kind, ev[2] if len(ev) > 2 else "")] = ev[1]
                    if self.load_t0 is None and not self.errored:
                        line = self._wait_line()
                        if line:
                            if getattr(self, "_pre_backlog", None) is None:
                                self._pre_backlog = self.v_status.get()
                            self._status_text(line)
                        elif getattr(self, "_pre_backlog", None) is not None:
                            self._status_text(self._pre_backlog)
                            self._pre_backlog = None
                elif kind == "cpu_line":  # #780: "CPU mode - MT n ms per line" once running
                    from .core import cpu_mode_line

                    if self.load_t0 is None and not self.errored:
                        text = cpu_mode_line("cpu", ev[1], path=ev[2] if len(ev) > 2 else None, block=False) or ""
                        wait = self._wait_line()  # #1253: a held-translation / backlog note stays visible
                        if wait and text and getattr(self, "_pre_backlog", None) is not None:
                            self._pre_backlog = text  # shown again (alone) once nothing waits any more
                        self._status_text(f"{text} | {wait}" if text and wait else (wait or text))
                        self._dedupe_notice()  # #1241: CPU mode is now in the status line; not twice
                elif kind == "notice" and ev[1] == SILENT_CLEAR:  # listened sound is back
                    self.v_notice.set(strip_silent_hint(self.v_notice.get()))
                elif kind == "notice":
                    from . import gpu_offer

                    if gpu_offer.is_missing_pack_notice(ev[1]):
                        self.gpu_runtime_failed = True  # the runtime could not load CUDA: Install must really repair
                        from ..gpu_pack import pack_dir

                        self.gpu_runtime_failed_mtime = _mtime(pack_dir())
                    if (gpu_offer.is_missing_pack_notice(ev[1]) and not gpu_offer.offline() and not self.gpu_busy
                            and not self.settings.get(gpu_offer.DISMISS_KEY)):
                        self._gpu_offer_show()  # #1525: the "GPU pack missing" line gets its [Install] button
                    if ev[1] not in self.v_notice.get():  # the repeated silent hint must not clobber other lines
                        self.v_notice.set(ev[1])
                        self._dedupe_notice()  # #1241
                    if self.speak_hint == SPEAK_HINT_CABLE:  # #710: e.g. the meeting voice turned itself off
                        self._show_panel()
                elif kind == "topics":  # #611
                    self.render_topics(show=bool(self.topic_panel.entries()))
                elif kind.startswith("gpu_"):  # #1525
                    self._gpu_event(ev)
                elif kind == "pack_line":
                    self._dl_event(ev)
                    self._status_text(ev[1])
                elif kind == "cable_done":
                    self._cable_done(ev[1])
                elif kind == "pack_fail_reason":
                    self._spk_fail_reason = ev[2]
                elif kind == "pack_done":
                    self._pack_done(ev[1], ev[2])
                elif kind == "dl_line":
                    self._dl_event(ev)
                    self._status_text(ev[1])
                elif kind == "doctor":
                    self._show_setup_check(ev[1], ev[2])
                elif kind == "clip_ai":
                    self.root.clipboard_clear()
                    self.root.clipboard_append(ev[1])
                    self._status_text(t("gui_copied_ai"))
                elif kind == "dl_done":
                    self._download_done(ev[1], ev[2])
                elif kind.startswith("upd_"):
                    self._update_event(ev)
                elif kind == "error":
                    self._status_text(t("gui_error", msg=ev[1]))
                    self.errored = True
                    self.set_dot("error")
                    self.root.deiconify()  # the error text is in the status line; a red dot alone says nothing
                    self.root.lift()
                elif kind == "stopped":
                    self._waits, self._pre_backlog = {}, None  # #1253: a new session starts with no old backlog
                    if self.gate is not None:
                        self.gate.clear()
                        self._gate_tick()
                        self.gate = None
                    self.load_t0 = None
                    self._slow_clear()
                    self._close_labeler()  # Stop / #983 restart: the worker thread exits, the model is released
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
                    self._sync_bar()
                    self.render_log()  # drops a leftover "Listening..." placeholder
                elif kind == "spk_ready":  # 0.3.8: a speaker label is ready: redraw the bubbles
                    changed = True
                else:
                    changed |= self.state.apply(ev)
                    if ev[0] in ("source", "draft", "final") and self.v_notice.get():  # other side is heard: hint is stale
                        self.v_notice.set(strip_silent_hint(self.v_notice.get()))
                    if ev[0] == "final" and usage_should_ask(self.settings) and getattr(self, "usage_win", None) is None:
                        self.root.after(1500, self._maybe_ask_usage)  # #680: once, after the first subtitle
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

    def _log_context(self, event):
        """#677: right-click a line of the main-window log -> 'Wrong translation (this line)'."""
        import re

        rows, _show_src = getattr(self, "_log_shown", ([], self.settings["show_src"]))
        self._log_row = None
        for name in self.log_text.tag_names(f"@{event.x},{event.y}"):
            m = re.fullmatch(r"row(\d+)", name)
            if m and int(m.group(1)) < len(rows):
                self._log_row = rows[int(m.group(1))]
        can_report = self._log_row is not None and not self._log_row[2]
        self.log_menu.entryconfigure(0, state="normal" if can_report else "disabled")
        self.log_menu.tk_popup(event.x_root, event.y_root)

    def _log_copy(self):
        """Copy the selected log text (or the whole log if nothing is selected) to the clipboard."""
        tx = self.log_text
        try:
            txt = tx.get("sel.first", "sel.last")
        except tk.TclError:
            txt = tx.get("1.0", "end-1c")
        if txt:
            self.root.clipboard_clear()
            self.root.clipboard_append(txt)

    def report_wrong_line(self):
        row = getattr(self, "_log_row", None)
        if row is not None:
            self.report_wrong(row)

    def report_wrong(self, row=None):
        """#677: one-click 'wrong translation' for a finished line (default: the newest); the app sends nothing, the browser opens on a pre-filled form."""
        import webbrowser

        from ..report_translation import report_line
        from .core import usage_prefill

        if row is None:
            finals = [r for r in self.state.history() if not r[2]]
            if not finals:
                messagebox.showinfo(t("gui_wrong_title"), t("gui_wrong_none"), parent=self.root)
                return
            row = finals[-1]
        src, out, _ = row
        s = self.settings
        report_line(src, out, version=__version__, mode=self.quick_mode or getattr(self, "_last_mode", None) or "listen",
                    langpair=f"{s.get('lang', 'auto')} -> {s.get('target', '')}",
                    confirm=lambda text: messagebox.askokcancel(t("gui_wrong_title"), t("gui_wrong_ask", text=text), parent=self.root),
                    open_url=webbrowser.open, extra=usage_prefill(s, True), confirm_extra=self._wrong_dialog)

    def _wrong_dialog(self, text, extra):
        """#680: confirm dialog with a ticked 'also include what I mainly use TingLong for' checkbox -> (ok, include)."""
        top = tk.Toplevel(self.root)
        top.title(t("gui_wrong_title"))
        top.transient(self.root)
        top.resizable(False, False)
        shown = text.rsplit("\n", len(extra))[0]
        ttk.Label(top, text=t("gui_wrong_ask", text=shown), wraplength=460, justify="left").pack(padx=12, pady=(12, 6))
        inc = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text=t("gui_wrong_usage", text="; ".join(f"{k}: {v}" for k, v in extra.items())),
                        variable=inc).pack(anchor="w", padx=12)
        res = {"ok": False}

        def done(ok):
            res["ok"] = ok
            top.destroy()

        bar = ttk.Frame(top)
        bar.pack(pady=10)
        ttk.Button(bar, text="OK", command=lambda: done(True)).pack(side="left", padx=6)
        ttk.Button(bar, text="Cancel", command=lambda: done(False)).pack(side="left", padx=6)
        top.grab_set()
        self.root.wait_window(top)
        return res["ok"], bool(inc.get())

    def quit(self):
        if self.closing:
            return
        self.closing = True
        self.stop_ev.set()
        self._close_labeler()
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
        self.root.after(1500, self._bg_defaults_start)  # #1358: fetch missing default listen models in the background
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
