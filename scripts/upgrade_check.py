r"""Does a new Windows core still work where an older version is already installed? (#923 review C1 / C2)

Every existing user gets the new core on top of the old folder: the auto-update runs the setup wizard over {app}
and Inno Setup never deletes what is already there. These checks run that path in CI (windows-build.yml `upgrade`).

    python scripts\upgrade_check.py overlay --old-zip OLD.zip --new-zip NEW.zip --dir DIR
        unzip the old core zip, unzip the new one over it (nothing deleted), then `tlwu.exe --smoke` in DIR\tlwu
    python scripts\upgrade_check.py unzip --zip OLD.zip --dir DIR
        only unzip (the "old install" a wizard is then run over)
    python scripts\upgrade_check.py wizard --setup SETUP.exe --dir DIR [--log LOG]
        run the setup wizard silently into DIR (fresh or holding an older version), wait for its model step
        (install.done), check that DIR holds the new core, then `tlwu.exe --smoke` there
    python scripts\upgrade_check.py smoke --app DIR

Exit 0 = OK, 1 = a check failed (the reason is printed).
"""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXE_NAME = "tlwu.exe"
ZIP_TOP = "tlwu"  # top folder of the core zip
# written by packaging/build_windows.py and runtime_layer.py at the top of every layered core (#923)
NEW_CORE_MARKERS = ("app-layer.json", "runtime-layer.json")
INSTALL_DONE = "install.done"  # the wizard's model step writes its exit code here (installer/tinglongwu.iss)
INSTALL_LOG = "install.log"
SMOKE_TIMEOUT_S = 600
WIZARD_TIMEOUT_S = 40 * 60  # program files + the default-listen model download on a CI runner
LOG_TAIL_LINES = 40
# /SP- no "do you want to install" box, /NOCANCEL nothing to click; the wizard's own postinstall start is skipifsilent
WIZARD_ARGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/NOCANCEL")


def unzip(zip_path: Path, dest: Path) -> Path:
    """Extract over whatever is in dest (existing files are overwritten, others stay). Returns dest/tlwu."""
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    app = dest / ZIP_TOP
    if not (app / EXE_NAME).is_file():
        raise SystemExit(f"{zip_path}: no {ZIP_TOP}/{EXE_NAME} inside")
    return app


def leftovers(old_names: set[str], new_names: set[str]) -> list[str]:
    """Files of the old zip that the new zip does not replace: what stays behind in an upgraded folder."""
    return sorted(old_names - new_names)


def smoke_problem(rc: int, out: str) -> str:
    """'' when `tlwu.exe --smoke` passed (the same verdict as the release check), else the reason."""
    # loaded by path: scripts/release_check.py has the same module name
    spec = importlib.util.spec_from_file_location("packaging_release_check", REPO / "packaging" / "release_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ok, detail = mod.smoke_verdict(rc, out)
    return "" if ok else detail


def run_smoke(app: Path, run=None) -> str:
    r = (run or subprocess.run)([str(app / EXE_NAME), "--smoke"], cwd=app, capture_output=True, encoding="utf-8", errors="replace",
            timeout=SMOKE_TIMEOUT_S)
    out = (r.stdout or "") + (r.stderr or "")
    print(out, flush=True)
    return smoke_problem(r.returncode, out)


def new_core_problem(app: Path) -> str:
    """'' when `app` holds a layered core (the wizard really installed there), else what is missing."""
    missing = [n for n in (EXE_NAME, *NEW_CORE_MARKERS) if not (app / n).is_file()]
    return f"{app} lacks {', '.join(missing)}: the new core was not installed there" if missing else ""


def tail(path: Path, n: int = LOG_TAIL_LINES) -> str:
    try:
        return "\n".join(path.read_text("utf-8", "replace").splitlines()[-n:])
    except OSError as e:
        return f"({path.name} unreadable: {e})"


def wizard_problem(setup_rc: int, app: Path) -> str:
    """'' when the silent wizard run succeeded: exit 0, new core in `app`, model step finished with exit code 0."""
    if setup_rc != 0:
        return f"the wizard exited with code {setup_rc}"
    prob = new_core_problem(app)
    if prob:
        return prob
    done = app / INSTALL_DONE
    if not done.is_file():
        return f"{INSTALL_DONE} missing: the wizard's model step (tlwu.exe setup --default-listen) did not finish"
    code = done.read_text("utf-8", "replace").strip()
    if code != "0":
        return f"the wizard's model step exited with code {code or '?'}:\n{tail(app / INSTALL_LOG)}"
    return ""


def cmd_overlay(old_zip: Path, new_zip: Path, dest: Path) -> int:
    with zipfile.ZipFile(old_zip) as z:
        old_names = {n for n in z.namelist() if not n.endswith("/")}
    with zipfile.ZipFile(new_zip) as z:
        new_names = {n for n in z.namelist() if not n.endswith("/")}
    unzip(old_zip, dest)
    app = unzip(new_zip, dest)
    left = leftovers(old_names, new_names)
    print(f"old zip: {len(old_names)} files, new zip: {len(new_names)} files, left behind from the old one: {len(left)}")
    for n in left[:LOG_TAIL_LINES]:
        print("  left:", n)
    prob = new_core_problem(app) or run_smoke(app)
    print("overlay:", f"FAIL: {prob}" if prob else "OK (the new core runs in a folder that still holds the old files)")
    return 1 if prob else 0


def cmd_wizard(setup: Path, app: Path, log: Path | None, run=None) -> int:
    had_old = (app / EXE_NAME).is_file()
    args = [str(setup), *WIZARD_ARGS, f"/DIR={app}"] + ([f"/LOG={log}"] if log else [])
    print("+", " ".join(args), f"({'over an existing install' if had_old else 'fresh folder'})", flush=True)
    r = (run or subprocess.run)(args, timeout=WIZARD_TIMEOUT_S)
    prob = wizard_problem(r.returncode, app) or run_smoke(app, run)
    print("wizard:", f"FAIL: {prob}" if prob else f"OK ({'upgrade' if had_old else 'fresh install'}, smoke passed)")
    return 1 if prob else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("overlay")
    p.add_argument("--old-zip", type=Path, required=True)
    p.add_argument("--new-zip", type=Path, required=True)
    p.add_argument("--dir", type=Path, required=True)
    p = sub.add_parser("unzip")
    p.add_argument("--zip", type=Path, required=True)
    p.add_argument("--dir", type=Path, required=True)
    p = sub.add_parser("wizard")
    p.add_argument("--setup", type=Path, required=True)
    p.add_argument("--dir", type=Path, required=True, help="install folder itself (the one that gets tlwu.exe)")
    p.add_argument("--log", type=Path)
    p = sub.add_parser("smoke")
    p.add_argument("--app", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "overlay":
        return cmd_overlay(a.old_zip, a.new_zip, a.dir.resolve())
    if a.cmd == "unzip":
        print("unzipped:", unzip(a.zip, a.dir.resolve()))
        return 0
    if a.cmd == "wizard":
        return cmd_wizard(a.setup, a.dir.resolve(), a.log)
    prob = run_smoke(a.app.resolve())
    print("smoke:", f"FAIL: {prob}" if prob else "OK")
    return 1 if prob else 0


if __name__ == "__main__":
    sys.exit(main())
