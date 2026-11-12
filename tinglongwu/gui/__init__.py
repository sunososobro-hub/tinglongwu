"""Optional tkinter GUI (stdlib only, fully offline). `tlwu-gui` or `tlwu --gui`."""
from __future__ import annotations

import os
import shutil
import sys


def tk_missing_hint() -> str:
    """#328: one line telling how to get tkinter (Debian/Ubuntu ship it as a separate apt package)."""
    if sys.platform.startswith("linux") and shutil.which("apt-get"):
        return f"tkinter is missing. Install it with: sudo apt install {_apt_tk_package()}"
    return "tkinter is missing. Install the Tk package for your Python (e.g. python3-tk, python3-tkinter or tk)."


def _apt_tk_package() -> str:
    """python3-tk for the system python3; python3.X-tk for a separately installed Python 3.X (e.g. deadsnakes)."""
    base = os.path.realpath(getattr(sys, "_base_executable", None) or sys.executable)
    system = shutil.which("python3")
    if system and os.path.realpath(system) == base:
        return "python3-tk"
    return f"python{sys.version_info[0]}.{sys.version_info[1]}-tk"


def main() -> int:
    try:
        import tkinter  # noqa: F401 - checked before the GUI module imports it at top level
    except ImportError:
        print(tk_missing_hint(), file=sys.stderr)
        return 1

    from .. import offline
    from .app import launch

    offline.set_mode("gui")

    return launch()
