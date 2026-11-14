"""#698: Windows auto-update for wizard installs. No network and no tkinter here: the GUI asks this module WHETHER to
check, and every request runs in a `python -m tinglongwu_setup.selfupdate` child (the runtime stays offline; the
network guard in tinglongwu.offline is never relaxed in this process).

Config (userdirs, KEY=VALUE): auto_update=1 -> check once per 24 h at GUI start and download in the background, then
ask "Restart now?"; auto_update=0 or missing -> no automatic request at all, only the "Check for updates" button.
TLWU_OFFLINE=1 -> no request, not even from the button. Only wizard installs (unins*.exe next to the app, Windows)
update this way; git checkouts keep update.bat / auto_update.sh."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .. import userdirs

CHECK_EVERY_S = 24 * 3600
AUTO_KEY = "auto_update"
LAST_KEY = "update_last_check"  # unix seconds of the last automatic check (also written when it failed)
MODULE = "tinglongwu_setup.selfupdate"


def app_root() -> Path:
    """Install dir ({app}): tinglongwu/ sits directly under it (editable install of the bundled source)."""
    return Path(__file__).resolve().parents[2]


def is_wizard_install(root: Path | None = None, platform: str | None = None) -> bool:
    root = app_root() if root is None else Path(root)
    return (platform or sys.platform) == "win32" and any(root.glob("unins*.exe"))


def offline(env=None) -> bool:
    return (os.environ if env is None else env).get("TLWU_OFFLINE") == "1"


def auto_on(cfg: dict) -> bool:
    return cfg.get(AUTO_KEY) == "1"


def should_auto_check(cfg: dict, now: float | None = None, env=None) -> bool:
    """True when GUI start should check now: auto_update=1, not offline, last check >= 24 h ago (or in the future)."""
    if offline(env) or not auto_on(cfg):
        return False
    now = time.time() if now is None else now
    try:
        last = float(cfg.get(LAST_KEY) or 0)
    except ValueError:
        last = 0.0
    return last > now or now - last >= CHECK_EVERY_S


def mark_checked(now: float | None = None) -> None:
    userdirs.write_config_value(LAST_KEY, str(int(time.time() if now is None else now)))


def set_auto(on: bool) -> None:
    userdirs.write_config_value(AUTO_KEY, "1" if on else "0")


def download_dir() -> Path:
    return Path(tempfile.gettempdir()) / "tinglongwu-update"


def _base(python: str | None = None) -> list[str]:
    return [python or sys.executable, "-m", MODULE]


def check_cmd(current: str, python: str | None = None) -> list[str]:
    return [*_base(python), "--check", current]


def download_cmd(dest: Path, python: str | None = None) -> list[str]:
    return [*_base(python), "--download", str(dest)]


def apply_cmd(exe: str, app: Path, pid: int, python: str | None = None) -> list[str]:
    return [*_base(python), "--apply", str(exe), "--app", str(app), "--pid", str(pid)]


def parse_check(line: str) -> str | None:
    """'UPDATE 0.3.4' -> '0.3.4'; NONE / SKIP / anything else -> None."""
    parts = (line or "").split()
    return parts[1] if len(parts) == 2 and parts[0] == "UPDATE" else None


def _child_env(env=None) -> dict:
    from ..offline import OFFLINE_ENV

    out = {k: v for k, v in (os.environ if env is None else env).items() if k not in OFFLINE_ENV}
    out.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    return out


def _lines(cmd, popen):
    p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=_child_env(),
              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for raw in p.stdout:
        line = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw).strip()
        if line:
            yield line
    p.wait()


def run_check(current: str, put, manual: bool, popen=None) -> str | None:
    """Worker thread. put(("upd_found", ver, manual)) or put(("upd_none", manual)). Errors are silent unless manual
    (then they read as "no update found"). Returns the new version or None."""
    if offline():
        put(("upd_none", manual))
        return None
    ver = None
    try:
        for line in _lines(check_cmd(current), popen or subprocess.Popen):
            ver = parse_check(line) or ver
    except Exception:  # noqa: BLE001
        ver = None
    put(("upd_found", ver, manual) if ver else ("upd_none", manual))
    return ver


def run_download(put, dest: Path | None = None, popen=None) -> str | None:
    """Worker thread. put(("upd_progress", pct)), then ("upd_ready", exe_path) or ("upd_fail", reason)."""
    if offline():
        put(("upd_fail", "offline"))
        return None
    path, last = None, ""
    try:
        for line in _lines(download_cmd(dest or download_dir()), popen or subprocess.Popen):
            last = line
            if line.startswith("PROGRESS "):
                try:
                    put(("upd_progress", int(line.split()[1])))
                except ValueError:
                    pass
            elif line.startswith("READY "):
                path = line[6:].strip()
    except Exception as e:  # noqa: BLE001
        last = f"{type(e).__name__}: {e}"
    if path:
        put(("upd_ready", path))
    else:
        put(("upd_fail", (last[5:] if last.startswith("FAIL ") else last)[:120]))
    return path


def start_apply(exe: str, pid: int | None = None, popen=None) -> int:
    """Start the detached installer helper (it waits for this process to exit). Return code of the launcher."""
    p = (popen or subprocess.Popen)(apply_cmd(exe, app_root(), pid or os.getpid()), env=_child_env(),
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return p.wait()
