"""Diagnostic file log (rotating, in userdirs.log_dir()) for errors that are deliberately not shown to the user (stdlib only)."""
from __future__ import annotations

import logging
import logging.handlers
import threading

from . import userdirs

LOG_NAME = "tinglongwu-app.log"
_lock = threading.Lock()
_ready = False


def _setup() -> logging.Logger:
    global _ready
    lg = logging.getLogger("tinglongwu")
    with _lock:
        if _ready:
            return lg
        _ready = True
        try:
            d = userdirs.log_dir()
            d.mkdir(parents=True, exist_ok=True)
            h = logging.handlers.RotatingFileHandler(d / LOG_NAME, maxBytes=512_000, backupCount=2, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            lg.addHandler(h)
            lg.setLevel(logging.DEBUG)
            lg.propagate = False
        except OSError:
            pass  # expected: unwritable log dir must never break the app
    return lg


def reset() -> None:
    """Drop handlers so the next call re-reads the log dir (tests)."""
    global _ready
    with _lock:
        lg = logging.getLogger("tinglongwu")
        for h in list(lg.handlers):
            lg.removeHandler(h)
            h.close()
        _ready = False


def swallowed(where: str = "") -> None:
    """Call inside an `except` block that continues on purpose: logs the active exception with traceback."""
    try:
        _setup().debug("swallowed exception in %s", where, exc_info=True)
    except Exception:  # expected: logging must never raise into the caller
        pass
