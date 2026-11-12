"""Optional tkinter GUI (stdlib only, fully offline). `tlwu-gui` or `tlwu --gui`."""
from __future__ import annotations

import shutil
import sys


def tk_missing_hint() -> str:
    """#328: one line telling how to get tkinter (Debian/Ubuntu ship it as a separate apt package)."""
    if sys.platform.startswith("linux") and shutil.which("apt-get"):
        return "tkinter is missing. Install it with: sudo apt install python3-tk"
    return "tkinter is missing. Install the Tk package for your Python (e.g. python3-tk, python3-tkinter or tk)."


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
