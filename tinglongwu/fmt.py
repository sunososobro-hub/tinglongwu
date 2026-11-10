"""Console block formatter: who said what. Shared by listen (sink) and speak (reverse) sides.

    12:03:10 ▶ 我   original
                    → translation
"""
from __future__ import annotations

import datetime
import os
import sys

from .i18n import MESSAGES, t

MESSAGES["en"].update(label_me="▶ Me", label_them="◀ Them")
MESSAGES["ru"].update(label_me="▶ Я", label_them="◀ Собеседник")
MESSAGES["zh-TW"].update(label_me="▶ 我", label_them="◀ 對方")

GREEN, CYAN, DIM, RESET = "\033[32m", "\033[36m", "\033[2m", "\033[0m"
INDENT = " " * 16
_vt_done = False


def color_enabled(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    try:
        if not stream.isatty():
            return False
    except Exception:  # noqa: BLE001
        return False
    return _enable_vt()


def _enable_vt() -> bool:
    global _vt_done
    if sys.platform != "win32" or _vt_done:
        return True
    try:
        os.system("")  # turns on VT processing in the Windows console
        _vt_done = True
    except Exception:  # noqa: BLE001
        return False
    return True


def block(side: str, original: str, translations=(), now=None, color: bool = False) -> str:
    """side 'me' | 'them'. translations: iterable of (tag|None, text). Returns the block incl. trailing blank line."""
    label = t("label_me" if side == "me" else "label_them")
    stamp = (now or datetime.datetime.now()).strftime("%H:%M:%S")
    if color:
        label = f"{GREEN if side == 'me' else CYAN}{label}{RESET}"
        stamp = f"{DIM}{stamp}{RESET}"
    lines = [f"{stamp} {label} {original}"]
    for tag, text in translations:
        lines.append(f"{INDENT}→ " + (f"[{tag}] " if tag else "") + text)
    return "\n".join(lines) + "\n\n"
