"""Opt-in reporting of network-guard blocks. Used by the menus:  python -m tinglongwu.netreport status|show|send|dismiss

status  exit 0 + one line (count, hosts) when there are unreported entries, exit 1 and no output otherwise
show    print the exact issue text
send    print the exact issue text, open the browser on a pre-filled GitHub new-issue URL, mark entries reported
dismiss mark entries reported without sending (the menu will not ask about them again)
Nothing is ever sent automatically: the user submits the issue on GitHub themselves.
"""
from __future__ import annotations

from . import diag as _diag

import json
import sys
from collections import OrderedDict
from urllib.parse import quote

from . import i18n, offline, userdirs

ISSUE_URL = "https://github.com/sunososobro-hub/tinglongwu/issues/new"
MAX_URL = 6000  # GitHub / browsers choke on much longer query strings
LABEL = "network-guard"


def log_path():
    return userdirs.log_dir() / offline.LOG_NAME


def read_entries() -> list[dict]:
    out = []
    try:
        for line in log_path().read_text("utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def unreported() -> list[dict]:
    return [e for e in read_entries() if not e.get("reported")]


def mark_reported() -> None:
    ents = read_entries()
    for e in ents:
        e["reported"] = True
    try:
        log_path().write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in ents), "utf-8")
    except OSError:
        pass


def summarize(entries: list[dict]) -> "OrderedDict[tuple, dict]":
    g: OrderedDict = OrderedDict()
    for e in entries:
        k = (e.get("host"), e.get("port"), e.get("package"), e.get("mode"))
        if k not in g:
            g[k] = {"count": 0, "first": e}
        g[k]["count"] += 1
        g[k]["last_ts"] = e.get("ts")
    return g


def hosts_line(entries: list[dict], limit: int = 5) -> str:
    hs = list(OrderedDict.fromkeys(f"{e.get('host')}:{e.get('port')}" for e in entries))
    return ", ".join(hs[:limit]) + (f" (+{len(hs) - limit})" if len(hs) > limit else "")


def build_body(entries: list[dict], max_stack: int = 12) -> str:
    g = summarize(entries)
    first = entries[0]
    lines = ["Reported from the tinglongwu menu (network guard blocked outgoing connections).", "",
             f"- tlwu: {first.get('version')} {first.get('sha') or ''}".rstrip(),
             f"- OS: {first.get('os')}", "", "| host | port | package | mode | count |", "|---|---|---|---|---|"]
    for (host, port, pkg, mode), v in g.items():
        lines.append(f"| {host} | {port} | {pkg} | {mode} | {v['count']} |")
    for (host, port, pkg, _m), v in list(g.items())[:3]:
        st = v["first"].get("stack") or []
        lines += ["", f"Stack for {host}:{port} (module:function:line):", "```", *st[-max_stack:], "```"]
    return "\n".join(lines) + "\n"


def build_url(entries: list[dict], max_url: int = MAX_URL) -> tuple[str, str]:
    """(url, body actually used). The body is shortened until the encoded URL fits."""
    g = summarize(entries)
    (host, port, pkg, _m), _v = next(iter(g.items()))
    title = f"network-guard: blocked connection to {host}" + (f":{port}" if port else "") + f" ({pkg})"
    if len(g) > 1:
        title += f" +{len(g) - 1} more"
    title = title[:120]
    stack = 12
    while True:
        body = build_body(entries, stack)
        url = f"{ISSUE_URL}?title={quote(title)}&labels={LABEL}&body={quote(body)}"
        if len(url) <= max_url:
            return url, body
        if stack > 0:
            stack = stack // 2
            continue
        # still too long: cut the body text itself
        keep = len(body)
        while keep > 200:
            keep = int(keep * 0.8)
            b = body[:keep] + "\n...(truncated)\n"
            url = f"{ISSUE_URL}?title={quote(title)}&labels={LABEL}&body={quote(b)}"
            if len(url) <= max_url:
                return url, b
        return f"{ISSUE_URL}?title={quote(title)}&labels={LABEL}", ""


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "status"
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            _diag.swallowed(__name__)
            pass
    ents = unreported()
    if cmd == "status":
        if not ents:
            return 1
        print(i18n.t("report_status", n=len(ents), hosts=hosts_line(ents)))
        return 0
    if cmd == "dismiss":
        mark_reported()
        print(i18n.t("report_marked"))
        return 0
    if cmd in ("show", "send"):
        if not ents:
            return 1
        url, body = build_url(ents)
        print(i18n.t("report_preview"))
        print("-----\n" + body + "-----")
        if cmd == "send":
            import webbrowser

            print(i18n.t("report_opening"))
            try:
                opened = webbrowser.open(url)
            except Exception:  # noqa: BLE001
                opened = False
            if not opened:
                print(i18n.t("report_manual"))
                print(url)
            mark_reported()
            print(i18n.t("report_marked"))
        return 0
    print("usage: python -m tinglongwu.netreport status|show|send|dismiss", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
