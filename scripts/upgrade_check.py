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
    python scripts\upgrade_check.py app-update --old-zip OLD.zip --core-dir NEWCORE --dir DIR
        (#947) same runtime key: `selfupdate --plan` says app (< 15 MB), `--apply-app` over an install of OLD.zip,
        tlwu.exe --smoke; then a truncated app-layer zip must leave the old install starting (rollback)
    python scripts\upgrade_check.py plan-full --old-zip OLD.zip --core-dir NEWCORE --dir DIR
        (#947) forced runtime key mismatch: the plan says full installer

Exit 0 = OK, 1 = a check failed (the reason is printed).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXE_NAME = "tlwu.exe"
ZIP_TOP = "tlwu"  # top folder of the core zip
# written by release/packaging/build_windows.py and runtime_layer.py at the top of every layered core (#923)
NEW_CORE_MARKERS = ("app-layer.json", "runtime-layer.json")
INSTALL_DONE = "install.done"  # the wizard's model step writes its exit code here (release/installer/tinglongwu.iss)
INSTALL_LOG = "install.log"
READY_LINE = "READY default-listen"  # `tlwu.exe setup --default-listen` prints it last, when the default models are in (#948)
SMOKE_TIMEOUT_S = 600
WIZARD_TIMEOUT_S = 40 * 60  # program files + the default-listen model download on a CI runner
LOG_TAIL_LINES = 40
APP_ONLY_MAX_MB = 15  # #947 Acceptance: the app-layer download stays under this
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
    spec = importlib.util.spec_from_file_location("packaging_release_check", REPO / "release" / "packaging" / "release_check.py")
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
    try:
        log = (app / INSTALL_LOG).read_text("utf-8", "replace")
    except OSError:
        log = ""
    if READY_LINE not in log:
        return f"{INSTALL_LOG} has no '{READY_LINE}' line, the default models were not reported ready:\n{tail(app / INSTALL_LOG)}"
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


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _selfupdate():
    sys.path.insert(0, str(REPO))  # tinglongwu_setup is a package at the repo root
    import tinglongwu_setup.selfupdate as su  # noqa: PLC0415
    return su


def fake_release_get(app_zip: Path, manifest: dict, version: str = "9.9.9"):
    """A `get` for tinglongwu_setup.selfupdate that serves a latest release whose app-layer asset is app_zip."""
    base = "https://example.invalid/dl/"
    release = {"tag_name": "v" + version, "assets": [
        {"name": "tinglongwu-setup.exe", "browser_download_url": base + "setup.exe", "size": 400 << 20},
        {"name": "SHA256SUMS.txt", "browser_download_url": base + "sums"},
        {"name": "tinglongwu-app-layer.zip", "browser_download_url": base + "app.zip", "size": app_zip.stat().st_size},
        {"name": "tinglongwu-app-layer.zip.manifest.json", "browser_download_url": base + "man"}]}
    body = {"https://api.github.com/repos/sunososobro-hub/tinglongwu/releases/latest": json.dumps(release),
            base + "man": json.dumps(manifest)}

    def get(url, timeout=0):
        if url not in body:
            raise OSError(f"unexpected url {url}")
        return body[url].encode("utf-8")
    return get


def make_app_layer(core: Path, out: Path) -> tuple[Path, dict]:
    """Zip + manifest of the app layer of an unzipped core folder (the same code the release uses, #945)."""
    bw = _load("packaging_build_windows", REPO / "release" / "packaging" / "build_windows.py")
    man_path = bw.make_app_layer_zip(core, out)
    return out / bw.APP_LAYER_ASSET, json.loads(man_path.read_text("utf-8"))


def plan_text(app: Path, app_zip: Path, manifest: dict) -> str:
    return _selfupdate().plan(app, get=fake_release_get(app_zip, manifest))


def plan_problem(plan: str, want: str) -> str:
    """'' when the plan line is the wanted kind ('app' or 'full'); for app also size < APP_ONLY_MAX_MB."""
    parts = plan.split()
    if parts[:2] != ["PLAN", want]:
        return f"plan is '{plan}', wanted PLAN {want}"
    if want == "app" and int(parts[2]) >= APP_ONLY_MAX_MB:
        return f"app layer is {parts[2]} MB, wanted < {APP_ONLY_MAX_MB} MB"
    return ""


def tree_digest(app: Path) -> str:
    """Hash of tlwu.exe, app-layer.json and every file name/size of the app folders (what a rollback must keep)."""
    h = hashlib.sha256()
    for item in ("tlwu.exe", "app-layer.json"):
        f = app / item
        h.update(f.read_bytes() if f.is_file() else b"-")
    for d in ("tinglongwu", "tlwu_app", "tinglongwu_setup"):
        for f in sorted((app / d).rglob("*")) if (app / d).is_dir() else []:
            if f.is_file():
                h.update(f"{f.relative_to(app).as_posix()}:{f.stat().st_size}".encode())
    return h.hexdigest()


def apply_text(app_zip: Path, app: Path, pid: int) -> str:
    """The real --apply-app helper script (selfupdate.apply_app_script) minus its last line, which would start the
    GUI: the job runs the smoke itself afterwards and must not leave a GUI process behind."""
    lines = _selfupdate().apply_app_script(app_zip, app, pid).splitlines()
    keep = [ln for ln in lines if not ln.startswith("if (Test-Path -LiteralPath $exe) { Start-Process")]
    # the helper swallows a swap error (rolls back, starts the old GUI): record it so a FAIL says why (#1660)
    i = keep.index("} catch {")
    keep.insert(i + 1, f"  Set-Content -LiteralPath {_selfupdate()._ps_quote(apply_error_path(app))} -Encoding UTF8 "
                       "-Value ($_ | Out-String)")
    return "\n".join(keep) + "\n"


def apply_error_path(app: Path) -> Path:
    return app.parent / "tlwu-apply-update.err"


def apply_failure(app: Path) -> str:
    """The swap error the last run_apply recorded, plus which app items / *.old / staging the folder holds now."""
    err = apply_error_path(app)
    why = err.read_text("utf-8-sig", "replace").strip() if err.is_file() else ""
    names = sorted(os.listdir(app)) if app.is_dir() else []
    held = [n for n in names if n in _selfupdate().APP_ITEMS or n.endswith(".old") or n.startswith(".update")]
    return f"\n  swap error: {why or '(none recorded)'}\n  {app} holds: {', '.join(held)}"


def run_apply(app_zip: Path, app: Path, run=None) -> None:
    """Run that script synchronously. The pid it waits for belongs to a process that already exited."""
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    script = app.parent / "tlwu-apply-update.ps1"
    apply_error_path(app).unlink(missing_ok=True)
    script.write_text(apply_text(app_zip, app, done.pid), "utf-8-sig")
    (run or subprocess.run)(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                            timeout=600, check=True)


def apply_log(app: Path) -> str:
    """The helper's own reason for a rolled-back swap (selfupdate.APPLY_APP_LOG, #1652), '' when it wrote none."""
    f = app / _selfupdate().APPLY_APP_LOG
    return f" ({' '.join(f.read_text('utf-8-sig', errors='replace').split())[:2000]})" if f.is_file() else ""


def _old_install(old_zip: Path, core: Path, dest: Path) -> Path:
    app = unzip(old_zip, dest)
    new_rt = core / "runtime-layer.json"
    if not (app / "runtime-layer.json").is_file() or (app / "runtime-layer.json").read_bytes() != new_rt.read_bytes():
        print("note: the released core has another runtime layer; its runtime-layer.json is set to the new build's so "
              "the check covers the swap itself (runtime key equality is #945's check)")
        (app / "runtime-layer.json").write_bytes(new_rt.read_bytes())
    return app


def cmd_app_update(old_zip: Path, core: Path, dest: Path) -> int:
    app_zip, man = make_app_layer(core, dest / "layer")
    app = _old_install(old_zip, core, dest / "ok")
    line = plan_text(app, app_zip, man)
    prob = plan_problem(line, "app")
    if prob:
        print("app-update: FAIL:", prob)
        return 1
    print(f"update: app layer only: {line.split()[2]} MB")
    run_apply(app_zip, app)
    new_ver = json.loads((core / "app-layer.json").read_text("utf-8"))
    got = json.loads((app / "app-layer.json").read_text("utf-8")) if (app / "app-layer.json").is_file() else {}
    if apply_error_path(app).is_file():  # a swallowed swap error is a failure even when app-layer.json looks new (#1660)
        prob = f"the --apply-app helper hit a swap error and rolled back{apply_failure(app)}{apply_log(app)}"
    else:
        prob = ("" if got == new_ver else f"app-layer.json is not the new one after --apply-app (have {got}, want "
                f"{new_ver}){apply_failure(app)}{apply_log(app)}") or run_smoke(app)
    if prob:
        print("app-update: FAIL:", prob)
        return 1
    print("update: app layer applied, smoke OK")
    # rollback: a truncated zip must leave the old install in place and starting
    bad = dest / "layer" / "truncated.zip"
    data = app_zip.read_bytes()
    bad.write_bytes(data[: len(data) // 2])
    old = _old_install(old_zip, core, dest / "rollback")
    before = tree_digest(old)
    run_apply(bad, old)
    leftover = [n for n in os.listdir(old) if n.endswith(".old") or n == ".update-staging"]
    prob = ("the install changed after a truncated app-layer zip" if tree_digest(old) != before else "") \
        or (f"leftovers after the failed swap: {leftover}" if leftover else "") or run_smoke(old)
    print("rollback:", f"FAIL: {prob}" if prob else "OK (old install untouched and starts)")
    return 1 if prob else 0


def cmd_plan_full(old_zip: Path, core: Path, dest: Path) -> int:
    app_zip, man = make_app_layer(core, dest / "layer")
    app = _old_install(old_zip, core, dest / "old")
    man = {**man, "runtime_key": "forced-mismatch-" + man["runtime_key"]}
    prob = plan_problem(plan_text(app, app_zip, man), "full")
    print("plan-full:", f"FAIL: {prob}" if prob else "OK")
    if not prob:
        print("update: full installer")
    return 1 if prob else 0


def main(argv=None) -> int:
    for st in (sys.stdout, sys.stderr):  # the CI job no longer sets PYTHONUTF8 / PYTHONIOENCODING (#947, D4 of #924)
        if hasattr(st, "reconfigure"):
            st.reconfigure(encoding="utf-8", errors="replace")
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
    for name in ("app-update", "plan-full"):
        p = sub.add_parser(name)
        p.add_argument("--old-zip", type=Path, required=True)
        p.add_argument("--core-dir", type=Path, required=True, help="unzipped new core (the tlwu folder)")
        p.add_argument("--dir", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "app-update":
        return cmd_app_update(a.old_zip, a.core_dir.resolve(), a.dir.resolve())
    if a.cmd == "plan-full":
        return cmd_plan_full(a.old_zip, a.core_dir.resolve(), a.dir.resolve())
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
