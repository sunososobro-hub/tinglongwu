"""#677 'Wrong translation': build a pre-filled `translation-error.yml` issue URL for one subtitle line.

The app sends nothing: the user sees the exact text in a dialog first, then the system browser opens on GitHub,
where they can still edit the form before submitting. Only the source text and tlwu's output of that line go in
(no audio, no other lines).
"""
from __future__ import annotations

import platform
from urllib.parse import quote

ISSUE_NEW = "https://github.com/sunososobro-hub/tinglongwu/issues/new"
MAX_URL = 6000  # GitHub rejects very long query strings; stay well below it
_OS = {"Windows": "Windows", "Darwin": "macOS", "Linux": "Linux"}
_MODES = {"listen", "speak", "meeting"}


def _q(s: str) -> str:
    return quote(s, safe="")


def os_name() -> str:
    return _OS.get(platform.system(), "Linux")


def happened_text(src: str, out: str, extra: dict | None = None) -> str:
    """Source + output lines, then one `key: value` line per `extra` entry (#680 usage answer; empty by default)."""
    lines = [f"Source: {src.strip()}", f"tlwu: {out.strip()}"]
    lines += [f"{k}: {v}" for k, v in (extra or {}).items()]
    return "\n".join(lines)


def build_url(version: str, mode: str, langpair: str, src: str, out: str, os_: str | None = None,
              max_url: int = MAX_URL, extra: dict | None = None) -> tuple[str, str]:
    """Return (url, happened) where `happened` is the (possibly truncated) text that is in the URL.

    `happened` is cut on a character boundary until the *encoded* URL fits `max_url`, so a UTF-8 sequence is never split."""
    mode = mode if mode in _MODES else "listen"

    def url_for(h: str) -> str:
        return (f"{ISSUE_NEW}?template=translation-error.yml&version={_q(version)}&os={_q(os_ or os_name())}"
                f"&mode={_q(mode)}&langpair={_q(langpair)}&happened={_q(h)}")

    happened = happened_text(src, out, extra)
    url = url_for(happened)
    if len(url) <= max_url:
        return url, happened
    lo, hi = 0, len(happened)  # longest prefix (plus an ellipsis) whose URL fits
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(url_for(happened[:mid] + "…")) <= max_url:
            lo = mid
        else:
            hi = mid - 1
    happened = happened[:lo] + "…"
    return url_for(happened), happened


def report_line(src: str, out: str, *, version: str, mode: str, langpair: str, confirm, open_url,
                extra: dict | None = None, confirm_extra=None) -> bool:
    """Ask `confirm(happened)`; open the URL only on True. Returns whether the browser was opened.

    #680: with `extra` (the usage answer) and `confirm_extra(happened, extra) -> (ok, include)` the user sees the text and
    a ticked checkbox for the extra lines; they go into the URL only when `include` is True. Never sent by the app."""
    if not (src or out).strip():
        return False
    if extra and confirm_extra is not None:
        ok, include = confirm_extra(happened_text(src, out, extra), extra)
        if not ok:
            return False
        url, happened = build_url(version, mode, langpair, src, out, extra=extra if include else None)
        return bool(open_url(url))
    url, happened = build_url(version, mode, langpair, src, out)
    if not confirm(happened):
        return False
    return bool(open_url(url))


def row_for_log_line(rows, show_src: bool, line: int):
    """Map a 1-based text line of the main-window log to its history row (None if out of range).

    Mirrors `App.render_log`: a row takes as many lines as its output renders, plus the source's lines when `show_src` and the source differs."""
    n = 1
    for row in rows:
        src, text, _ = row
        height = text.count("\n") + 1
        if show_src and src and src != text:
            height += src.count("\n") + 1
        if n <= line < n + height:
            return row
        n += height
    return None
