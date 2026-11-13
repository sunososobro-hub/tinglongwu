"""Start-up splash: ~1.5 s half-block animation of the TingLong mascot (frames precomputed by scripts/make_mascot.py).

Shown only on an interactive truecolor terminal; everything is best-effort and never stops the CLI.
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import time
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "splash_frames.json.gz"


def load_frames(path: Path = DATA) -> dict:
    """{'w': cols, 'h': rows, 'fps': n, 'frames': [ANSI string, ...]} (no Pillow needed)."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        d = json.load(fh)
    if not d.get("frames") or d["h"] != d["frames"][0].count("\n") + 1:
        raise ValueError("bad splash data")
    return d


def enable_vt() -> bool:
    """Windows: switch the console to VT processing; True when escapes will work. Other OSes: always True."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        return bool(mode.value & 4) or bool(k.SetConsoleMode(h, mode.value | 4))
    except Exception:
        return False


def truecolor(env=None) -> bool:
    env = os.environ if env is None else env
    if env.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return True
    if env.get("WT_SESSION") or env.get("TERM_PROGRAM") in ("vscode", "iTerm.app", "WezTerm", "ghostty"):
        return True
    return "truecolor" in env.get("TERM", "") or "24bit" in env.get("TERM", "")


def should_show(no_splash: bool = False, stream=None, env=None, size=None) -> bool:
    """False for --no-splash, NO_COLOR, CI / tests, non-TTY stdout, non-truecolor or too small terminals."""
    env = os.environ if env is None else env
    stream = sys.stdout if stream is None else stream
    if no_splash or env.get("NO_COLOR") is not None or env.get("TLWU_NO_SPLASH"):
        return False
    if env.get("CI") or env.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return False
    try:
        if not (stream.isatty() and sys.stdin.isatty()):
            return False
        cols, rows = size or os.get_terminal_size(stream.fileno())
    except Exception:
        return False
    if env.get("TERM") == "dumb" or not truecolor(env):
        return False
    return cols >= 66 and rows >= 22


def play(no_splash: bool = False, stream=None) -> bool:
    """Play the animation if allowed; returns True when it ran. Clears its own lines afterwards."""
    stream = sys.stdout if stream is None else stream
    try:
        if not should_show(no_splash, stream):
            return False
        data = load_frames()
        if not enable_vt():
            return False
        h, delay = data["h"], 1.0 / data["fps"]
        stream.write("\x1b[?25l")  # hide the cursor
        try:
            for i, fr in enumerate(data["frames"]):
                if i:
                    stream.write(f"\x1b[{h - 1}A\r")
                stream.write(fr)
                stream.flush()
                time.sleep(delay)
        finally:
            stream.write(f"\x1b[{h - 1}A\r\x1b[J\x1b[?25h")  # erase the frame, show the cursor
            stream.flush()
        return True
    except Exception:
        return False
