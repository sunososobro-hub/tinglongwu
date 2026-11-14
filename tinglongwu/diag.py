"""Diagnostic file log (rotating, in userdirs.log_dir()) for errors that are deliberately not shown to the user (stdlib only)."""
from __future__ import annotations

import logging
import logging.handlers
import threading

from . import userdirs

LOG_NAME = "tinglongwu-app.log"
# A dedicated, non-propagating logger: a handler on the package logger "tinglongwu" would stop other
# tinglongwu.* warnings (e.g. llamacheck's "CUDA build not used", #398) from reaching stderr via lastResort.
LOGGER = "tinglongwu.diag"
_lock = threading.Lock()
_ready = False


class _QuietHandler(logging.handlers.RotatingFileHandler):
    def handleError(self, record: logging.LogRecord) -> None:  # expected: a failing diagnostic log stays silent
        pass


def _setup() -> logging.Logger:
    global _ready
    lg = logging.getLogger(LOGGER)
    with _lock:
        if _ready:
            return lg
        try:
            d = userdirs.log_dir()
            d.mkdir(parents=True, exist_ok=True)
            h = _QuietHandler(d / LOG_NAME, maxBytes=512_000, backupCount=2, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            lg.addHandler(h)
            lg.setLevel(logging.DEBUG)
            lg.propagate = False
            _ready = True
        except OSError:
            pass  # expected: unwritable log dir must never break the app
    return lg


def reset() -> None:
    """Drop handlers so the next call re-reads the log dir (tests)."""
    global _ready
    with _lock:
        lg = logging.getLogger(LOGGER)
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
