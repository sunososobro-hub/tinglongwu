"""Auto-update line of the main window (#698 / #779 / #1672), split out of app.py (#1917).

`UpdateMixin` holds the App methods that build and drive the "New version ... [Update now] [Later]" line;
`App` inherits it, so every call site and test sees the same methods on `App`.
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from . import autoupdate
from .. import __version__, userdirs
from ..i18n import t

UPDATE_LINE_WRAP_PX = 200  # #455: text + two buttons stay inside the 420 px main window
UPDATE_QUIT_DELAY_MS = 1500  # #698: let the "Updating..." line paint before the window closes


class UpdateMixin:
    """App methods for the auto-update line (state lives on the App instance)."""

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

            webbrowser.open(autoupdate.recovery_url())
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
