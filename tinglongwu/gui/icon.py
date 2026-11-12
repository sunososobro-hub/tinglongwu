"""App icon: window icon (Tk) + Windows taskbar identity. Everything here is best-effort; a missing icon must never stop the GUI."""
from __future__ import annotations

import sys
from pathlib import Path

APP_ID = "tinglongwu.tlwu.gui"


def icon_path(name: str = "tinglongwu.ico"):
    """Path of a packaged icon file (package_data, so it exists in a source checkout and in a pip install)."""
    return Path(__file__).resolve().parent / "assets" / name


def set_app_id() -> None:
    """Windows: own AppUserModelID so the taskbar shows our icon instead of grouping under python(w).exe."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def apply_icon(root) -> None:
    """Set the window/taskbar icon on a Tk root (.ico via iconbitmap on Windows, .png via iconphoto everywhere)."""
    try:
        if sys.platform == "win32":
            root.iconbitmap(default=str(icon_path("tinglongwu.ico")))
    except Exception:
        pass
    try:
        import tkinter as tk
        img = tk.PhotoImage(master=root, file=str(icon_path("tinglongwu.png")))
        root.iconphoto(True, img)
        root._tlwu_icon = img  # keep a reference, Tk drops unreferenced images
    except Exception:
        pass
