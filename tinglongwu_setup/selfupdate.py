"""Windows self-update for wizard installs (#698). Networked, so it lives here and runs only as a child process.

  --check CURRENT [--count]
                         ask GitHub for the latest release (6 s, any failure = "SKIP <why>"); prints one line:
                         "UPDATE X.Y.Z" when it is newer than CURRENT, "NONE" otherwise. Always exit code 0.
                         --count (only the once-a-day automatic check, #806) asks the anonymous counter Worker
                         (cloudflare/update-counter) instead: it returns the same GitHub answer and adds 1 to a count
                         keyed only by (date, app version, OS). Worker unreachable -> GitHub API exactly as without it.
  --download DIR         download tinglongwu-setup.exe of the latest release into DIR (resumable, see resume.fetch),
                         verify it against the release's SHA256SUMS.txt; prints "PROGRESS n" lines then
                         "READY <path>". Exit 1 with "FAIL <why>" (a SHA256 mismatch aborts, the file is deleted).
  --apply EXE --app DIR --pid N
                         start a detached PowerShell helper that waits for process N (the GUI) to exit, runs EXE
                         silently, then starts the GUI again. The installer keeps models\\ and the user config.

The GUI (tinglongwu/gui/autoupdate.py) decides whether to ask at all (auto_update=1, once per 24 h, never with
TLWU_OFFLINE=1). Pre-releases and drafts are ignored. Run: `python -m tinglongwu_setup.selfupdate ...`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from . import resume

REPO = "sunososobro-hub/tinglongwu"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
EXE_NAME = "tinglongwu-setup.exe"
SUMS_NAME = "SHA256SUMS.txt"
CHECK_TIMEOUT_S = 6
SUMS_TIMEOUT_S = 30
WAIT_GUI_EXIT_S = 120  # the helper gives the GUI this long to close before installing anyway
INSTALLER_ARGS = ("/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-")  # /SILENT: progress window, no questions
# #806 counter Worker base URL, set once the Worker is deployed (cloudflare/update-counter/README.md). Empty = no
# counter, every check goes straight to GitHub. TLWU_UPDATE_COUNTER overrides it (tests, staging).
COUNTER_BASE = ""
COUNTER_TIMEOUT_S = 4  # short, so a dead Worker still leaves time for the GitHub fallback
_UA = {"User-Agent": "tlwu-setup/0.1", "Accept": "application/vnd.github+json"}
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse_version(tag: str | None) -> tuple[int, int, int] | None:
    """'v0.3.3' / '0.3.3' -> (0, 3, 3). Anything else (pre-release suffix, garbage) -> None."""
    m = _VERSION_RE.match((tag or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def is_newer(latest: str | None, current: str | None) -> bool:
    new, cur = parse_version(latest), parse_version(current)
    return bool(new and cur and new > cur)


def _get(url: str, timeout: int = CHECK_TIMEOUT_S) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=timeout) as r:
        return r.read()


def os_tag(platform: str | None = None) -> str:
    p = platform or sys.platform
    return "windows" if p == "win32" else "mac" if p == "darwin" else "linux" if p.startswith("linux") else "other"


def counter_url(current: str, env=None, platform: str | None = None) -> str | None:
    """The Worker's /v1/latest URL carrying only the app version and the OS, or None when no counter is set."""
    env = os.environ if env is None else env
    base = (env.get("TLWU_UPDATE_COUNTER") or COUNTER_BASE).rstrip("/")
    if not base.startswith("https://"):
        return None
    ver = current if parse_version(current) else "other"
    return f"{base}/v1/latest?" + urllib.parse.urlencode({"v": ver, "os": os_tag(platform)})


def _latest_json(get, count_url: str | None) -> dict:
    if count_url:
        try:
            rel = json.loads(get(count_url, timeout=COUNTER_TIMEOUT_S).decode("utf-8"))
            if isinstance(rel, dict) and rel.get("tag_name"):
                return rel  # anything else ({} / [] / an error object) -> GitHub below
        except Exception:  # noqa: BLE001  # Worker down / blocked / bad answer: ask GitHub as before
            pass
    return json.loads(get(API_LATEST).decode("utf-8"))


def latest_release(get=_get, count_url: str | None = None) -> dict | None:
    """{'version', 'exe_url', 'sums_url'} of the latest published release, or None (pre-release, draft, no wizard
    asset, unparseable tag). Network errors propagate; callers turn them into a silent skip. count_url: ask the
    counter Worker first (check only; download() always reads GitHub, so the Worker never picks what is installed)."""
    rel = _latest_json(get, count_url)
    if rel.get("prerelease") or rel.get("draft"):
        return None
    tag = rel.get("tag_name") or ""
    if parse_version(tag) is None:
        return None
    assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets") or []}
    if not assets.get(EXE_NAME) or not assets.get(SUMS_NAME):
        return None
    return {"version": tag.lstrip("v"), "exe_url": assets[EXE_NAME], "sums_url": assets[SUMS_NAME]}


def parse_sums(text: str, name: str) -> str | None:
    """SHA256 of `name` from a `sha256sum` listing ('<hex>  <name>' or '<hex> *<name>'), lower case, or None."""
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return None


def check(current: str, get=_get, env=None, count: bool = False) -> str:
    """The one line --check prints. count=True only for the automatic daily check (auto_update=1)."""
    env = os.environ if env is None else env
    if env.get("TLWU_OFFLINE") == "1":
        return "SKIP offline"
    try:
        rel = latest_release(get, counter_url(current, env) if count else None)
    except Exception as e:  # noqa: BLE001  # timeout, HTTP error, bad JSON: never bother the user
        return f"SKIP {type(e).__name__}"
    if rel is None:
        return "NONE"
    return f"UPDATE {rel['version']}" if is_newer(rel["version"], current) else "NONE"


def download(dest_dir: Path, get=_get, fetch=resume.fetch, say=print) -> Path:
    """Download + verify the wizard of the latest release. ValueError on a missing/mismatching checksum."""
    rel = latest_release(get)
    if rel is None:
        raise ValueError("no published release with a setup wizard")
    sha = parse_sums(get(rel["sums_url"], timeout=SUMS_TIMEOUT_S).decode("utf-8", "replace"), EXE_NAME)
    if not sha:
        raise ValueError(f"{EXE_NAME} is not listed in {SUMS_NAME}")
    dest = Path(dest_dir) / f"tinglongwu-setup-{rel['version']}.exe"
    total = [0]

    def on_bytes(n: int) -> None:
        pct = min(99, n * 100 // total[0]) if total[0] else 0
        if pct != getattr(on_bytes, "last", -1):
            on_bytes.last = pct  # type: ignore[attr-defined]
            say(f"PROGRESS {pct}")

    def opener(url, start):
        r = resume._open_range(url, start)
        try:
            total[0] = start + int(r.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            total[0] = 0
        return r

    if fetch is resume.fetch:
        return fetch(rel["exe_url"], dest, sha, opener=opener, on_bytes=on_bytes)
    return fetch(rel["exe_url"], dest, sha, on_bytes=on_bytes)


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def apply_script(exe: Path, app_dir: Path, pid: int) -> str:
    """PowerShell for the helper: wait for the GUI, install silently, start the GUI again (also after a failed
    install, so the user is never left without the app)."""
    gui = Path(app_dir) / ".venv" / "Scripts" / "tlwu-gui.exe"
    log = Path(exe).with_suffix(".log")
    args = ", ".join(_ps_quote(a) for a in (*INSTALLER_ARGS, f"/LOG={log}"))
    return "\n".join([
        "$ErrorActionPreference = 'SilentlyContinue'",
        f"Wait-Process -Id {int(pid)} -Timeout {WAIT_GUI_EXIT_S}",
        f"Start-Process -FilePath {_ps_quote(exe)} -ArgumentList @({args}) -Wait",
        f"if (Test-Path {_ps_quote(gui)}) {{ Start-Process -FilePath {_ps_quote(gui)} "
        f"-WorkingDirectory {_ps_quote(app_dir)} }}",
        "",
    ])


def launch_apply(exe: Path, app_dir: Path, pid: int, popen=subprocess.Popen) -> Path:
    script = Path(exe).with_name("tlwu-apply-update.ps1")
    script.write_text(apply_script(exe, app_dir, pid), "utf-8-sig")
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script)]
    base = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    try:  # leave the GUI's job object (if any) so closing the GUI does not kill the helper
        popen(cmd, creationflags=base | breakaway, close_fds=True)
    except OSError:
        popen(cmd, creationflags=base, close_fds=True)
    return script


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    p = argparse.ArgumentParser(prog="python -m tinglongwu_setup.selfupdate")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", metavar="CURRENT")
    g.add_argument("--download", metavar="DIR")
    g.add_argument("--apply", metavar="EXE")
    p.add_argument("--count", action="store_true", help="automatic daily check: go through the counter Worker")
    p.add_argument("--app", metavar="DIR")
    p.add_argument("--pid", type=int)
    a = p.parse_args(argv)
    if a.check:
        print(check(a.check, count=a.count))
        return 0
    if a.download:
        if os.environ.get("TLWU_OFFLINE") == "1":
            print("FAIL offline (TLWU_OFFLINE=1)")
            return 1
        try:
            path = download(Path(a.download))
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {type(e).__name__}: {e}"[:200])
            return 1
        print(f"READY {path}")
        return 0
    if not a.app or not a.pid:
        p.error("--apply needs --app and --pid")
    launch_apply(Path(a.apply), Path(a.app), a.pid)
    print("APPLYING")
    return 0


if __name__ == "__main__":
    sys.exit(main())
