"""'Report a problem': build a pre-filled GitHub new-issue URL and open it in the system browser.

Nothing is uploaded by the app: the user reviews the issue text on GitHub and submits it themselves.
Paths and the user name are redacted from the text before it goes into the URL.
"""
from __future__ import annotations

import getpass
import os
import platform
import re
import sys
from pathlib import Path
from urllib.parse import quote

from . import userdirs

ISSUE_URL = "https://github.com/sunososobro-hub/tinglongwu/issues/new"
MAX_URL = 6000
LOG_LINES = 20


def _version() -> str:
    try:
        from . import __version__

        return str(__version__)
    except ImportError:
        return "unknown"


def redact(text: str) -> str:
    """Hide the home directory, the user name and Windows/POSIX user paths."""
    home = os.path.expanduser("~")
    if home and home not in ("~", "/"):
        text = text.replace(home, "~")
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = ""
    if user and len(user) >= 2:
        text = re.sub(re.escape(user), "<user>", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)([A-Z]:\\Users\\)[^\\\s]+", r"\1<user>", text)
    text = re.sub(r"(/(?:home|Users)/)[^/\s]+", r"\1<user>", text)
    return text


def last_log_lines(n: int = LOG_LINES) -> list[str]:
    """Last n lines of the newest *.log in the log directory (empty when there is none)."""
    try:
        logs = sorted(userdirs.log_dir().glob("*.log"), key=lambda p: p.stat().st_mtime)
        if not logs:
            return []
        return logs[-1].read_text("utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def build_body(info: dict | None = None, log_lines: list[str] | None = None) -> str:
    info = dict(info or {})
    rows = [("App version", _version()),
            ("OS", f"{platform.system()} {platform.release()}"),
            ("Python", platform.python_version())]
    for k, v in info.items():
        rows.append((k, str(v)))
    lines = ["**What happened?** (please describe)", "", "", "---"]
    lines += [f"- {k}: {v}" for k, v in rows]
    if log_lines:
        lines += ["", "Last log lines:", "```"] + list(log_lines) + ["```"]
    return redact("\n".join(lines) + "\n")


def build_url(title: str = "[bug] ", info: dict | None = None, log_lines: list[str] | None = None,
              max_url: int = MAX_URL) -> str:
    """Issue URL with the body cut (log lines first, then the text) so the whole URL stays <= max_url."""
    logs = list(log_lines or [])
    while True:
        body = build_body(info, logs)
        url = f"{ISSUE_URL}?title={quote(title)}&body={quote(body)}"
        if len(url) <= max_url:
            return url
        if logs:
            logs = logs[1:]  # drop the oldest log line
            continue
        keep = len(body)
        while keep > 100:
            keep = int(keep * 0.8)
            url = f"{ISSUE_URL}?title={quote(title)}&body={quote(body[:keep] + chr(10) + '...(truncated)')}"
            if len(url) <= max_url:
                return url
        return f"{ISSUE_URL}?title={quote(title)}"


def open_report(info: dict | None = None, say=print) -> str:
    """Open the browser on the pre-filled issue; print the URL when no browser could be opened."""
    url = build_url(info=info, log_lines=last_log_lines())
    try:
        import webbrowser

        opened = webbrowser.open(url)
    except Exception:  # noqa: BLE001
        opened = False
    if not opened:
        say(url)
    return url


def main(argv=None) -> int:
    open_report(say=lambda m: print(m, file=sys.stderr))
    return 0
