"""Optional tkinter GUI (stdlib only, fully offline). `tlwu-gui` or `tlwu --gui`."""
from __future__ import annotations


def main() -> int:
    from .. import offline
    from .app import launch

    offline.set_mode("gui")

    return launch()
