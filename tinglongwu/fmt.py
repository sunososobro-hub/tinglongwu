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


def block(side: str, original: str, translations=(), now=None, color: bool = False, who: str | None = None) -> str:
    """side 'me' | 'them'. translations: iterable of (tag|None, text). Returns the block incl. trailing blank line."""
    return head(side, original, now=now, color=color, who=who) + tail(translations)


def head(side: str, original: str, now=None, color: bool = False, who: str | None = None) -> str:
    """First line of a block ('12:03:10 ◀ Them original' + newline), printed alone while MT still runs (#53)."""
    label = t("label_me" if side == "me" else "label_them")
    if who and side != "me":  # #384 --speakers: the speaker label or user-given name replaces the generic 'Them'
        label = f"◀ {who}"
    stamp = (now or datetime.datetime.now()).strftime("%H:%M:%S")
    if color:
        label = f"{GREEN if side == 'me' else CYAN}{label}{RESET}"
        stamp = f"{DIM}{stamp}{RESET}"
    return f"{stamp} {label} {original}\n"


def tail(translations=()) -> str:
    """The '→ [TAG] text' lines under a head, plus the blank line that ends the block."""
    return "".join(f"{INDENT}→ " + (f"[{tag}] " if tag else "") + text + "\n" for tag, text in translations) + "\n"
