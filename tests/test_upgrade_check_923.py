"""#923 review C1 / C2: scripts/upgrade_check.py (new core over an old install) and its windows-build job."""
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import upgrade_check as uc  # noqa: E402

SMOKE_OK = "OK   numpy (0.1s)\nOK   llama_cpp (0.1s)\nsmoke: OK (0 required module(s) failed)\n"


def _zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def _runner(rc=0, out=SMOKE_OK, calls=None):
    def run(cmd, **kw):
        if calls is not None:
            calls.append(cmd)
        return subprocess.CompletedProcess(cmd, rc, out, "")
    return run


def test_overlay_keeps_old_files_and_runs_the_new_core(tmp_path, monkeypatch, capsys):
    old = _zip(tmp_path / "old.zip", {"tlwu/tlwu.exe": "old exe", "tlwu/numpy/core.pyd": "old pyd",
                                      "tlwu/_ssl.pyd": "nuitka leftover"})
    new = _zip(tmp_path / "new.zip", {"tlwu/tlwu.exe": "new exe", "tlwu/numpy/core.cp312-win_amd64.pyd": "new",
                                      "tlwu/app-layer.json": "{}", "tlwu/runtime-layer.json": "{}"})
    calls = []
    monkeypatch.setattr(uc.subprocess, "run", _runner(calls=calls))
    assert uc.main(["overlay", "--old-zip", str(old), "--new-zip", str(new), "--dir", str(tmp_path / "d")]) == 0
    app = tmp_path / "d" / "tlwu"
    assert (app / "tlwu.exe").read_text() == "new exe"  # replaced
    assert (app / "_ssl.pyd").read_text() == "nuitka leftover" and (app / "numpy" / "core.pyd").is_file()  # stay
    assert calls == [[str(app / "tlwu.exe"), "--smoke"]]
    out = capsys.readouterr().out
    assert "left behind from the old one: 2" in out and "left: tlwu/_ssl.pyd" in out
    # the smoke failing in the overlaid folder fails the check
    monkeypatch.setattr(uc.subprocess, "run", _runner(rc=1, out="FAIL numpy: ImportError\nsmoke: FAIL\n"))
    assert uc.main(["overlay", "--old-zip", str(old), "--new-zip", str(new), "--dir", str(tmp_path / "e")]) == 1
    # an old-layout zip as "new" is not accepted as a layered core
    assert uc.main(["overlay", "--old-zip", str(old), "--new-zip", str(old), "--dir", str(tmp_path / "f")]) == 1


def test_smoke_problem_uses_the_release_verdict():
    assert uc.smoke_problem(0, SMOKE_OK) == ""
    assert "exit code 3" in uc.smoke_problem(3, SMOKE_OK)
    assert "llama_cpp" in uc.smoke_problem(0, "OK   numpy (0.1s)\nsmoke: OK\n")  # llama must load (#398)


READY_LOG = "[1/2] model\nREADY default-listen seconds=9.0 download_mb=4\n"


def _installed(app: Path, done: str | None = "0\n", ready: bool = True):
    app.mkdir(parents=True, exist_ok=True)
    for n in (uc.EXE_NAME, *uc.NEW_CORE_MARKERS):
        (app / n).write_text("x")
    if done is not None:
        (app / uc.INSTALL_DONE).write_text(done)
    if ready:
        (app / uc.INSTALL_LOG).write_text(READY_LOG)


def test_wizard_problem_cases(tmp_path):
    app = tmp_path / "app"
    assert "lacks tlwu.exe" in uc.wizard_problem(0, app)  # /DIR ignored: nothing was installed there
    _installed(app, done=None)
    assert "did not finish" in uc.wizard_problem(0, app)
    (app / uc.INSTALL_LOG).write_text("line 1\nHTTP 503 from the model host\n")
    (app / uc.INSTALL_DONE).write_text("1 \r\n")
    assert "exited with code 1" in uc.wizard_problem(0, app) and "HTTP 503" in uc.wizard_problem(0, app)
    (app / uc.INSTALL_DONE).write_text("0 \r\n")  # cmd's `echo !errorlevel! >file` leaves a space and CRLF
    assert "READY default-listen" in uc.wizard_problem(0, app)  # exit 0 but no READY line in install.log (#948)
    (app / uc.INSTALL_LOG).write_text(READY_LOG)
    assert uc.wizard_problem(0, app) == ""
    assert "code 5" in uc.wizard_problem(5, app)
    (app / "runtime-layer.json").unlink()  # an old Nuitka folder the wizard did not touch
    assert "runtime-layer.json" in uc.wizard_problem(0, app)


def test_wizard_command_is_silent_into_the_given_folder_then_smokes(tmp_path):
    app, calls = tmp_path / "inst", []
    _installed(app)
    rc = uc.cmd_wizard(tmp_path / "setup.exe", app, tmp_path / "s.log", run=_runner(calls=calls))
    assert rc == 0
    assert calls[0] == [str(tmp_path / "setup.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                        "/NOCANCEL", f"/DIR={app}", f"/LOG={tmp_path / 's.log'}"]
    assert calls[1] == [str(app / "tlwu.exe"), "--smoke"]
    assert uc.cmd_wizard(tmp_path / "setup.exe", app, None, run=_runner(rc=2)) == 1


def test_windows_build_has_an_upgrade_job_over_the_latest_release():
    wf = yaml.safe_load((REPO / ".github" / "workflows" / "windows-build.yml").read_text(encoding="utf-8"))
    build, up = wf["jobs"]["build"], wf["jobs"]["upgrade"]
    assert up["needs"] == "build" and "needs.build.outputs.build == 'true'" in up["if"]
    assert set(build["outputs"]) >= {"build", "artifact"}
    runs = [str(s.get("run", "")) for s in up["steps"]]
    text = "\n".join(runs)
    idx = lambda needle: next(i for i, r in enumerate(runs) if needle in r)  # noqa: E731
    base = idx("gh release download")
    overlay = idx("upgrade_check.py overlay")
    gui = idx("win_smoke.ps1")
    wizard = idx("build_installer.py --core-dir")
    fresh, over = [i for i, r in enumerate(runs) if "upgrade_check.py wizard" in r]
    assert base < overlay < gui < wizard < fresh < over  # C1: zip over zip + GUI; C2: wizard fresh, then over old
    assert "upgrade_check.py unzip" in runs[over]  # the old install the second wizard run goes over
    for i in (overlay, gui, fresh, over):
        assert "exit $LASTEXITCODE" in runs[i] and "continue-on-error" not in up["steps"][i]
    assert "v0." not in text  # the baseline is whatever release is published now, never a pinned old version
    assert up["permissions"] == {"contents": "read"}


@pytest.mark.parametrize("old,new,left", [({"a", "b"}, {"b", "c"}, ["a"]), (set(), {"x"}, []), ({"x"}, {"x"}, [])])
def test_leftovers(old, new, left):
    assert uc.leftovers(old, new) == left


# ---- #947: app-layer-only update and full update in the upgrade job ----

def _core(root: Path, version="1.2.3", key="rk-abc") -> Path:
    app = root / "tlwu"
    for pkg in ("tinglongwu", "tlwu_app", "tinglongwu_setup"):
        (app / pkg).mkdir(parents=True, exist_ok=True)
        (app / pkg / "__init__.py").write_text(f"v = '{version}'\n")
    (app / "tlwu.exe").write_bytes(b"MZ" + version.encode())
    (app / "使用說明.txt").write_text("hi", "utf-8")
    (app / "app-layer.json").write_text(json.dumps({"version": version, "packages": ["tinglongwu"]}))
    (app / "runtime-layer.json").write_text(json.dumps({"key": key, "lite": True, "python": "3.12.4"}))
    return app


def _old_zip(tmp_path) -> Path:
    old = _core(tmp_path / "oldsrc", version="1.2.2")
    z = tmp_path / "old.zip"
    with zipfile.ZipFile(z, "w") as f:
        for p in old.rglob("*"):
            if p.is_file():
                f.write(p, "tlwu/" + p.relative_to(old).as_posix())
    return z


def _fake_apply(app_zip, app, run=None):
    """Stand-in for the PowerShell helper: swap on a good zip, leave the install alone on a broken one."""
    try:
        with zipfile.ZipFile(app_zip) as z:
            z.extractall(app)
    except zipfile.BadZipFile:
        pass


def test_plan_problem_cases():
    assert uc.plan_problem("PLAN app 3", "app") == ""
    assert "wanted < 15 MB" in uc.plan_problem("PLAN app 15", "app")
    assert "wanted PLAN app" in uc.plan_problem("PLAN full 400", "app")
    assert uc.plan_problem("PLAN full 400", "full") == ""
    assert "wanted PLAN full" in uc.plan_problem("PLAN app 3", "full")


def test_plan_says_app_for_the_same_runtime_key_and_full_for_a_mismatch(tmp_path):
    core = _core(tmp_path / "new")
    app_zip, man = uc.make_app_layer(core, tmp_path / "layer")
    assert man["runtime_key"] == "rk-abc" and app_zip.stat().st_size == man["size"]
    old = uc.unzip(_old_zip(tmp_path), tmp_path / "inst")
    assert uc.plan_text(old, app_zip, man).startswith("PLAN app ")
    assert uc.plan_text(old, app_zip, {**man, "runtime_key": "other"}).startswith("PLAN full 400")


def test_apply_text_is_the_real_helper_without_the_gui_start(tmp_path):
    text = uc.apply_text(tmp_path / "a.zip", tmp_path / "app", 123)
    assert "Expand-Archive" in text and "Rename-Item" in text and "$exe = Join-Path" in text
    assert "Start-Process" not in text


def test_app_update_applies_then_rolls_back_a_truncated_zip(tmp_path, monkeypatch, capsys):
    core = _core(tmp_path / "new")
    monkeypatch.setattr(uc, "run_apply", _fake_apply)
    monkeypatch.setattr(uc.subprocess, "run", _runner())
    rc = uc.main(["app-update", "--old-zip", str(_old_zip(tmp_path)), "--core-dir", str(core), "--dir", str(tmp_path / "w")])
    out = capsys.readouterr().out
    assert rc == 0 and "update: app layer only: " in out and "rollback: OK" in out
    ok = tmp_path / "w" / "ok" / "tlwu"
    assert json.loads((ok / "app-layer.json").read_text())["version"] == "1.2.3"  # the swap happened
    # an apply that damages the old install on a broken zip is reported
    def damaging(app_zip, app, run=None):
        if app_zip.name == "truncated.zip":
            (app / "tlwu.exe").write_bytes(b"half")
        else:
            _fake_apply(app_zip, app)
    monkeypatch.setattr(uc, "run_apply", damaging)
    rc = uc.main(["app-update", "--old-zip", str(_old_zip(tmp_path)), "--core-dir", str(core), "--dir", str(tmp_path / "w2")])
    assert rc == 1 and "the install changed" in capsys.readouterr().out


def test_plan_full_command_prints_the_full_installer_line(tmp_path, capsys):
    core = _core(tmp_path / "new")
    rc = uc.main(["plan-full", "--old-zip", str(_old_zip(tmp_path)), "--core-dir", str(core), "--dir", str(tmp_path / "w")])
    assert rc == 0 and "update: full installer" in capsys.readouterr().out


def test_upgrade_job_runs_both_update_paths_without_python_utf8_env():
    wf = yaml.safe_load((REPO / ".github" / "workflows" / "windows-build.yml").read_text(encoding="utf-8"))
    up = wf["jobs"]["upgrade"]
    assert "env" not in up  # D4 of #924: no PYTHONUTF8 / PYTHONIOENCODING
    runs = [str(s.get("run", "")) for s in up["steps"]]
    unzipped = next(i for i, r in enumerate(runs) if "upgrade_check.py unzip" in r and "core" in r)
    app = next(i for i, r in enumerate(runs) if "upgrade_check.py app-update" in r)
    full = next(i for i, r in enumerate(runs) if "upgrade_check.py plan-full" in r)
    assert unzipped < app < full
    for i in (app, full):
        assert "exit $LASTEXITCODE" in runs[i] and "continue-on-error" not in up["steps"][i]


def test_app_update_fails_when_the_helper_recorded_a_swap_error_even_if_the_version_matches(tmp_path, monkeypatch, capsys):
    core = _core(tmp_path / "new")

    def apply_with_error(app_zip, app, run=None):
        _fake_apply(app_zip, app)  # app-layer.json ends up new (e.g. same-version baseline) ...
        uc.apply_error_path(app).write_text("boom: access denied", "utf-8")  # ... but the helper caught an error

    monkeypatch.setattr(uc, "run_apply", apply_with_error)
    monkeypatch.setattr(uc.subprocess, "run", _runner())
    rc = uc.main(["app-update", "--old-zip", str(_old_zip(tmp_path)), "--core-dir", str(core), "--dir", str(tmp_path / "w")])
    out = capsys.readouterr().out
    assert rc == 1 and "swap error: boom: access denied" in out
