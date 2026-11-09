"""Optional tkinter GUI (stdlib only, fully offline). `tlwu-gui` or `tlwu --gui`."""
from __future__ import annotations


def main() -> int:
    from .app import launch

    return launch()
