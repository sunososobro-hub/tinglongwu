"""#698: Windows auto-update for wizard installs. No network and no tkinter here: the GUI asks this module WHETHER to
check, and every request runs in a `python -m tinglongwu_setup.selfupdate` child (bundled tlwu.exe, #768:
`tlwu.exe --run-selfupdate ...`); the runtime stays offline and the network guard in tinglongwu.offline is never
relaxed in this process.

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
from pathlib import Path, PureWindowsPath

from .. import userdirs


def _log(msg: str) -> None:
    """One line into the diagnostic file log (tinglongwu-app.log); never raises."""
    try:
        from .. import diag

        diag._setup().info(msg)
    except Exception:  # noqa: BLE001  # expected: logging must never break an update
        pass

UPDATE_CHECK_INTERVAL_H = 24  # #806: at most one automatic check per day, so counter hits ~ daily active installs
CHECK_EVERY_S = UPDATE_CHECK_INTERVAL_H * 3600
AUTO_KEY = "auto_update"
LAST_KEY = "update_last_check"  # unix seconds of the last automatic check (also written when it failed)
UPDATE_PROMPT_MIN_INTERVAL_S = 24 * 3600  # #779: at most one update line per day, however many releases ship
PROMPT_KEY = "update_last_prompt"  # unix seconds of the last update line shown by an automatic check
PENDING_KEY = "update_pending"  # "<version>|<installer path>": downloaded + verified, applied at the next launch
DOWNLOAD_URL = "https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/tinglongwu-setup.exe"  # #1526
MODULE = "tinglongwu_setup.selfupdate"
BUNDLED_FLAG = "--run-selfupdate"  # tlwu_app.main routes this to tinglongwu_setup.selfupdate.main (#768)


def is_bundled() -> bool:
    """Same semantics as tlwu_app.main.is_bundled (frozen / compiled / TLWU_BUNDLED=1, which tlwu.exe sets
    before the GUI starts, or sys.executable = tlwu.exe, #1007). Kept here so the runtime package does not import the launcher."""
    return (bool(getattr(sys, "frozen", False)) or "__compiled__" in globals() or os.environ.get("TLWU_BUNDLED") == "1"
            or PureWindowsPath(sys.executable).name.lower() == "tlwu.exe")


def app_root() -> Path:
    """Install dir ({app}). Bundled: the folder of tlwu.exe (sys.executable is tlwu.exe there).
    Source install: tinglongwu/ sits directly under it (editable install of the bundled source)."""
    if is_bundled():
        return Path(sys.executable).resolve().parent
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


def claim_prompt(cfg: dict, now: float | None = None) -> bool:
    """#779: True (and the time is stored) when an automatic update line may be shown now: last one >= 24 h ago."""
    now = time.time() if now is None else now
    try:
        last = float(cfg.get(PROMPT_KEY) or 0)
    except ValueError:
        last = 0.0
    if not (last > now or now - last >= UPDATE_PROMPT_MIN_INTERVAL_S):
        return False
    userdirs.write_config_value(PROMPT_KEY, str(int(now)))
    return True


def _ver(v: str | None) -> tuple[int, int, int] | None:
    parts = (v or "").strip().lstrip("vV").split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1]), int(parts[2])


def get_pending(cfg: dict) -> tuple[str, str] | None:
    ver, sep, path = (cfg.get(PENDING_KEY) or "").partition("|")
    return (ver, path) if sep and ver and path else None


def set_pending(ver: str, path: str, cfg: dict | None = None) -> None:
    """#779: keep only the newest downloaded installer; an older pending file is deleted."""
    old = get_pending(userdirs.read_config() if cfg is None else cfg)
    if old and (_ver(ver) or (0, 0, 0)) < (_ver(old[0]) or (0, 0, 0)) and old[1] != path:
        return  # an older download never displaces a newer pending installer
    if old and old[1] != path:
        for f in (old[1], old[1] + ".sums", old[1] + ".sums.sig"):  # #1348: the kept sums go with the file
            try:
                Path(f).unlink()
            except OSError:
                pass
    userdirs.write_config_value(PENDING_KEY, f"{ver}|{path}")


def pending_to_apply(cfg: dict, current: str, exists=None) -> str | None:
    """#779: installer path to apply at GUI start, or None. A stale (not newer than `current`) or missing entry is
    cleared silently. Offline mode never applies anything."""
    pend = get_pending(cfg)
    if not pend:
        if cfg.get(PENDING_KEY):
            userdirs.write_config_value(PENDING_KEY, "")
        return None
    ver, path = pend
    a, b = _ver(ver), _ver(current)
    if a is None or b is None or a <= b or not (exists or os.path.exists)(path):
        userdirs.write_config_value(PENDING_KEY, "")
        return None
    return path


def clear_pending() -> None:
    userdirs.write_config_value(PENDING_KEY, "")


def set_auto(on: bool) -> None:
    userdirs.write_config_value(AUTO_KEY, "1" if on else "0")


def download_dir() -> Path:
    return Path(tempfile.gettempdir()) / "tinglongwu-update"


def _base(python: str | None = None) -> list[str]:
    if is_bundled():  # tlwu.exe has no `-m`; it re-runs itself as the networked selfupdate child
        return [python or sys.executable, BUNDLED_FLAG]
    return [python or sys.executable, "-m", MODULE]


def check_cmd(current: str, python: str | None = None, count: bool = False) -> list[str]:
    """count=True (automatic daily check only, #806) routes it through the anonymous counter Worker."""
    return [*_base(python), "--check", current, *(["--count"] if count else [])]


def download_cmd(dest: Path, python: str | None = None) -> list[str]:
    return [*_base(python), "--download", str(dest)]


def plan_cmd(app: Path, python: str | None = None) -> list[str]:
    return [*_base(python), "--plan", "--app", str(app)]


def download_app_cmd(dest: Path, python: str | None = None) -> list[str]:
    return [*_base(python), "--download-app", str(dest)]


def apply_cmd(exe: str, app: Path, pid: int, python: str | None = None) -> list[str]:
    """A .zip is the app layer (#946, --apply-app); anything else is the full installer (--apply)."""
    flag = "--apply-app" if str(exe).lower().endswith(".zip") else "--apply"
    return [*_base(python), flag, str(exe), "--app", str(app), "--pid", str(pid)]


def parse_plan(line: str) -> tuple[str, int] | None:
    """'PLAN app 12' -> ('app', 12); 'PLAN full 140' -> ('full', 140); anything else -> None."""
    parts = (line or "").split()
    if len(parts) == 3 and parts[0] == "PLAN" and parts[1] in ("app", "full") and parts[2].isdigit():
        return parts[1], int(parts[2])
    return None


def cleanup_old(root: Path | None = None) -> None:
    """#946: delete the *.old items an app-layer update left behind (call after a successful start)."""
    import shutil

    root = app_root() if root is None else Path(root)
    for name in ("tinglongwu", "tlwu_app", "tinglongwu_setup", "app-layer.json", "tlwu.exe"):
        old = root / (name + ".old")
        try:
            shutil.rmtree(old) if old.is_dir() else old.unlink(missing_ok=True)
        except OSError:
            pass


def parse_check(line: str) -> str | None:
    """'UPDATE 0.3.4' -> '0.3.4'; NONE / SKIP / anything else -> None."""
    parts = (line or "").split()
    return parts[1] if len(parts) == 2 and parts[0] == "UPDATE" else None


def _child_env(env=None) -> dict:
    from ..offline import OFFLINE_ENV

    out = {k: v for k, v in (os.environ if env is None else env).items() if k not in OFFLINE_ENV}
    out.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    return out


def _lines(cmd, popen, status: dict | None = None):
    p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=_child_env(),
              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for raw in p.stdout:
        line = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw).strip()
        if line:
            yield line
    rc = p.wait()
    if status is not None:
        status["rc"] = rc


def run_check(current: str, put, manual: bool, popen=None, cfg: dict | None = None) -> str | None:
    """Worker thread. put(("upd_found", ver, manual)), put(("upd_none", manual)) only for an explicit NONE line, or
    (#1526) put(("upd_fail_check", reason)) when a manual check failed (SKIP / non-zero exit / timeout / exception);
    a failed automatic check stays silent (put(("upd_none", False))) and logs the reason. Returns the new version or None. Only the automatic check with
    auto_update=1 is counted (#806); a manual "Check for updates" click goes straight to GitHub and is never counted."""
    if offline():
        put(("upd_none", manual))
        return None
    ver, none, why = None, False, ""
    try:
        count = not manual and auto_on(userdirs.read_config() if cfg is None else cfg)
        for line in _lines(check_cmd(current, count=count), popen or subprocess.Popen, status=(st := {})):
            ver = parse_check(line) or ver
            if line == "NONE":
                none = True
            elif line.startswith("SKIP"):
                why = line[4:].strip()[:120] or "unknown"
        if not ver and not none and not why and st.get("rc"):
            why = f"exit {st['rc']}"
    except Exception as e:  # noqa: BLE001
        ver, why = None, f"{type(e).__name__}: {e}"[:120]
    if ver:
        put(("upd_found", ver, manual))
        return ver
    if none and not why:
        put(("upd_none", manual))
        return None
    why = why or "no answer"
    _log(f"update check failed: {why}")
    put(("upd_fail_check", why) if manual else ("upd_none", False))
    return None


def _plan(popen, root: Path | None = None) -> tuple[str, int] | None:
    try:
        res = None
        for line in _lines(plan_cmd(app_root() if root is None else root), popen):
            res = parse_plan(line) or res
        return res
    except Exception:  # noqa: BLE001
        return None


def run_download(put, dest: Path | None = None, popen=None) -> str | None:
    """Worker thread. put(("upd_progress", pct)), then ("upd_ready", path) or ("upd_fail", reason). #946: with an
    unchanged runtime layer only the app-layer zip is fetched (path ends in .zip); any plan/download failure falls
    back to the full installer exactly as before. Logs one line saying which update and how many MB."""
    if offline():
        put(("upd_fail", "offline"))
        return None
    popen = popen or subprocess.Popen
    dest = dest or download_dir()
    plan = _plan(popen)
    if plan and plan[0] == "app":
        _log(f"update: app layer only: {plan[1]} MB")
        path = _download(download_app_cmd(dest), put, popen)[0]
        if path:
            put(("upd_ready", path))
            return path
        _log("update: app layer download failed, using the full installer")
    _log(f"update: full installer: {plan[1] if plan else 0} MB")
    return _download_full(put, dest, popen)


def _download(cmd, put, popen) -> tuple[str | None, str]:
    path, last = None, ""
    try:
        for line in _lines(cmd, popen):
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
    return path, last


def _download_full(put, dest: Path, popen) -> str | None:
    path, last = _download(download_cmd(dest), put, popen)
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
