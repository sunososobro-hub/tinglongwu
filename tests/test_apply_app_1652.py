"""#1652: the --apply-app helper must not depend on the Expand-Archive module, and a rolled-back swap must say why.

windows-build `upgrade` failed with only "app-layer.json is not the new one after --apply-app": the helper's
try/catch swallowed the error. The helper now extracts with .NET ZipFile and writes the error to APPLY_APP_LOG."""
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))
import upgrade_check as uc  # noqa: E402
import tinglongwu_setup.selfupdate as su  # noqa: E402
from test_upgrade_check_923 import _core, _old_zip, _runner  # noqa: E402

PWSH = shutil.which("pwsh")


def test_failed_swap_reports_the_helpers_reason(tmp_path, monkeypatch, capsys):
    core = _core(tmp_path / "new")

    def failing_apply(app_zip, app, run=None):  # what the helper does on an error: install untouched + log
        (app / su.APPLY_APP_LOG).write_text("apply-app failed: Expand-Archive : module could not be loaded", "utf-8")

    monkeypatch.setattr(uc, "run_apply", failing_apply)
    monkeypatch.setattr(uc.subprocess, "run", _runner())
    rc = uc.main(["app-update", "--old-zip", str(_old_zip(tmp_path)), "--core-dir", str(core), "--dir", str(tmp_path / "w")])
    out = capsys.readouterr().out
    assert rc == 1 and "app-layer.json is not the new one" in out and "module could not be loaded" in out


def test_helper_logs_the_error_in_its_catch_and_clears_it_on_success():
    ps = su.apply_app_script(Path(r"C:\T\app.zip"), Path(r"C:\A"), 1)
    lines = ps.splitlines()
    catch = lines.index("} catch {")
    assert any("Set-Content -LiteralPath $log" in ln for ln in lines[catch:])
    assert any("Remove-Item -LiteralPath $log" in ln for ln in lines[:catch])
    assert f"'{su.APPLY_APP_LOG}'" in ps


def _install(tmp_path):
    old = _core(tmp_path / "old", version="1.0.0")
    new = _core(tmp_path / "new", version="2.0.0")
    zp = tmp_path / "app.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for f in new.rglob("*"):
            if f.is_file() and f.name != "runtime-layer.json":
                z.write(f, f.relative_to(new).as_posix())
    return old, zp


def _run_helper(tmp_path, app, zp, env):
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    script = tmp_path / "helper.ps1"
    script.write_text(uc.apply_text(zp, app, done.pid), "utf-8-sig")  # the helper minus the GUI start
    return subprocess.run([PWSH, "-NoProfile", "-File", str(script)], env=env, capture_output=True, text=True,
                          timeout=120)


@pytest.mark.skipif(PWSH is None, reason="needs PowerShell")
def test_swap_works_when_the_archive_module_is_broken(tmp_path):
    # A module path whose Microsoft.PowerShell.Archive throws on import, like a 5.1 shell given a pwsh 7 PSModulePath.
    mods = tmp_path / "mods" / "Microsoft.PowerShell.Archive"
    mods.mkdir(parents=True)
    # defines and exports a throwing Expand-Archive (#1656): the pre-#1654 helper calls it and fails, the ZipFile one never does
    (mods / "Microsoft.PowerShell.Archive.psm1").write_text(
        "function Expand-Archive { throw 'broken Archive module' }\nExport-ModuleMember -Function Expand-Archive\n", "utf-8")
    env = {**os.environ, "PSModulePath": str(tmp_path / "mods") + os.pathsep + os.environ.get("PSModulePath", "")}
    app, zp = _install(tmp_path)
    r = _run_helper(tmp_path, app, zp, env)
    assert r.returncode == 0, r.stderr
    assert json.loads((app / "app-layer.json").read_text())["version"] == "2.0.0", r.stdout
    assert not (app / su.APPLY_APP_LOG).exists() and not (app / su.STAGING_DIR).exists()


@pytest.mark.skipif(PWSH is None, reason="needs PowerShell")
def test_bad_zip_rolls_back_and_writes_the_log(tmp_path):
    app, zp = _install(tmp_path)
    data = zp.read_bytes()
    zp.write_bytes(data[: len(data) // 2])
    r = _run_helper(tmp_path, app, zp, dict(os.environ))
    assert r.returncode == 0, r.stderr
    assert json.loads((app / "app-layer.json").read_text())["version"] == "1.0.0"
    assert "apply-app failed" in (app / su.APPLY_APP_LOG).read_text("utf-8-sig") and "apply-app failed" in r.stdout


@pytest.mark.skipif(PWSH is None, reason="needs PowerShell")
def test_undeletable_log_after_a_good_swap_does_not_roll_back(tmp_path):  # #1656
    app, zp = _install(tmp_path)
    log = app / su.APPLY_APP_LOG
    log.mkdir()  # a non-empty directory cannot be removed with Remove-Item -Force: stands in for a locked log
    (log / "held").write_text("x", "utf-8")
    r = _run_helper(tmp_path, app, zp, dict(os.environ))
    assert r.returncode == 0, r.stderr
    assert json.loads((app / "app-layer.json").read_text())["version"] == "2.0.0", r.stdout
