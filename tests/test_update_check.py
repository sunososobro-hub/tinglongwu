"""#698: Windows auto-update. Network side (tinglongwu_setup.selfupdate) and GUI side (tinglongwu.gui.autoupdate),
all offline: every request is a fake."""
import io
import json
import urllib.error
from pathlib import Path

import pytest

from tinglongwu_setup import resume, selfupdate as su

ROOT = Path(__file__).resolve().parent.parent
SHA = "a" * 64


def release(tag="v0.3.4", prerelease=False, draft=False, assets=(su.EXE_NAME, su.SUMS_NAME)):
    return json.dumps({"tag_name": tag, "prerelease": prerelease, "draft": draft,
                       "assets": [{"name": n, "browser_download_url": f"https://x/{n}"} for n in assets]}).encode()


def getter(body=None, sums=None, exc=None, calls=None):
    def get(url, timeout=su.CHECK_TIMEOUT_S):
        if calls is not None:
            calls.append(url)
        if exc:
            raise exc
        return sums if url.endswith(su.SUMS_NAME) else body
    return get


# -- version compare -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("latest,current,newer", [
    ("v0.3.4", "0.3.3", True), ("0.4.0", "0.3.9", True), ("v0.10.0", "0.9.9", True),  # numeric, not text
    ("v0.3.3", "0.3.3", False), ("v0.3.2", "0.3.3", False),
    ("v0.3.4-rc1", "0.3.3", False), ("v0.4.0b1", "0.3.3", False), ("", "0.3.3", False), ("v0.3.4", "dev", False)])
def test_is_newer(latest, current, newer):
    assert su.is_newer(latest, current) is newer


def test_check_newer_same_older():
    assert su.check("0.3.3", getter(release("v0.3.4")), env={}) == "UPDATE 0.3.4"
    assert su.check("0.3.4", getter(release("v0.3.4")), env={}) == "NONE"
    assert su.check("0.3.5", getter(release("v0.3.4")), env={}) == "NONE"


def test_prerelease_draft_and_missing_assets_ignored():
    assert su.check("0.3.3", getter(release(prerelease=True)), env={}) == "NONE"
    assert su.check("0.3.3", getter(release(draft=True)), env={}) == "NONE"
    assert su.check("0.3.3", getter(release(tag="v0.3.4-rc1")), env={}) == "NONE"
    assert su.check("0.3.3", getter(release(assets=(su.SUMS_NAME,))), env={}) == "NONE"


@pytest.mark.parametrize("exc", [TimeoutError("timed out"), TimeoutError(),
                                 urllib.error.HTTPError("u", 403, "rate limit", {}, io.BytesIO()),
                                 urllib.error.URLError("no route"), ValueError("bad json")])
def test_timeout_and_http_errors_do_not_crash(exc):
    assert su.check("0.3.3", getter(exc=exc), env={}).startswith("SKIP ")


def test_offline_env_makes_no_request():
    calls = []
    assert su.check("0.3.3", getter(release(), calls=calls), env={"TLWU_OFFLINE": "1"}) == "SKIP offline"
    assert calls == []


def test_check_uses_6s_timeout_on_the_latest_api(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout):
        seen.update(url=req.full_url, timeout=timeout)
        raise urllib.error.URLError("x")

    monkeypatch.setattr(su.urllib.request, "urlopen", fake_urlopen)
    assert su.check("0.3.3", env={}).startswith("SKIP")
    assert seen == {"url": su.API_LATEST, "timeout": 6}


# -- checksum ----------------------------------------------------------------------------------------------------
def test_parse_sums():
    text = f"{'b' * 64}  tinglongwu-win64.zip\n{SHA.upper()} *{su.EXE_NAME}\n"
    assert su.parse_sums(text, su.EXE_NAME) == SHA
    assert su.parse_sums(text, "nope.exe") is None
    assert su.parse_sums("short  tinglongwu-setup.exe", su.EXE_NAME) is None


def test_download_passes_release_sha_to_fetch(tmp_path):
    got = {}

    def fetch(url, dest, sha256, on_bytes=None):
        got.update(url=url, dest=dest, sha=sha256)
        return dest

    sums = f"{SHA}  {su.EXE_NAME}\n".encode()
    out = su.download(tmp_path, get=getter(release("v0.3.4"), sums), fetch=fetch, say=lambda *_: None)
    assert got == {"url": f"https://x/{su.EXE_NAME}", "dest": tmp_path / "tinglongwu-setup-0.3.4.exe", "sha": SHA}
    assert out == got["dest"]


def test_download_aborts_when_exe_not_in_sums(tmp_path):
    with pytest.raises(ValueError, match="not listed"):
        su.download(tmp_path, get=getter(release(), b"x  other.exe\n"), fetch=lambda *a, **k: None)


def test_sha256_mismatch_aborts_and_deletes(tmp_path):
    class Resp(io.BytesIO):
        status, headers = 200, {}

    with pytest.raises(ValueError, match="SHA256 mismatch"):
        resume.fetch("https://x/e.exe", tmp_path / "e.exe", SHA, opener=lambda url, start: Resp(b"evil"))
    assert not (tmp_path / "e.exe").exists() and not (tmp_path / "e.exe.part").exists()


def test_download_cli_offline_and_failure(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("TLWU_OFFLINE", "1")
    assert su.main(["--download", str(tmp_path)]) == 1
    assert capsys.readouterr().out.startswith("FAIL offline")
    monkeypatch.delenv("TLWU_OFFLINE")
    monkeypatch.setattr(su, "download", lambda d: (_ for _ in ()).throw(ValueError("SHA256 mismatch for x")))
    assert su.main(["--download", str(tmp_path)]) == 1
    assert "SHA256 mismatch" in capsys.readouterr().out


# -- apply -------------------------------------------------------------------------------------------------------
def test_apply_script_waits_installs_silently_and_restarts():
    ps = su.apply_script(Path(r"C:\T\it's\setup.exe"), Path(r"C:\Users\a\AppData\Local\Programs\tinglongWu"), 4242)
    lines = ps.splitlines()
    assert lines[1].startswith("Wait-Process -Id 4242")
    assert "'C:\\T\\it''s\\setup.exe'" in lines[2] and "-Wait" in lines[2]  # quote doubled for PowerShell
    for a in ("/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"):
        assert f"'{a}'" in lines[2]
    assert "tlwu.exe" in lines[3] and "tlwu-gui.exe" in lines[4]  # #768: prebuilt core first, then the .venv GUI
    assert lines[4].startswith("elseif ")


def test_launch_apply_detached(tmp_path):
    calls = []
    su.launch_apply(tmp_path / "setup.exe", tmp_path, 7, popen=lambda cmd, **kw: calls.append((cmd, kw)))
    cmd, kw = calls[0]
    assert cmd[0] == "powershell.exe" and cmd[-1].endswith("tlwu-apply-update.ps1")
    assert (tmp_path / "tlwu-apply-update.ps1").exists() and kw["close_fds"]


# -- installer: models and config survive ------------------------------------------------------------------------
def test_installer_keeps_models_and_respects_choice_on_silent_update():
    iss = (ROOT / "installer" / "tinglongwu.iss").read_text("utf-8-sig")
    assert '[InstallDelete]' not in iss  # an (update) install never deletes models\ or the config
    assert 'Name: "autoupdate"' in iss
    task = next(l for l in iss.splitlines() if l.startswith('Name: "autoupdate"'))
    assert "unchecked" not in task  # opt-in box is ticked by default
    assert "if not WizardSilent then" in iss  # a silent self-update keeps the user's earlier choice


# -- GUI side (no network, no tkinter) ---------------------------------------------------------------------------
def test_should_auto_check():
    from tinglongwu.gui import autoupdate as au

    day = au.CHECK_EVERY_S
    assert au.should_auto_check({"auto_update": "1"}, now=10 * day, env={})
    assert au.should_auto_check({"auto_update": "1", au.LAST_KEY: str(9 * day)}, now=10 * day, env={})
    assert not au.should_auto_check({"auto_update": "1", au.LAST_KEY: str(10 * day - 60)}, now=10 * day, env={})
    assert au.should_auto_check({"auto_update": "1", au.LAST_KEY: str(20 * day)}, now=10 * day, env={})  # clock
    assert not au.should_auto_check({"auto_update": "0"}, now=10 * day, env={})
    assert not au.should_auto_check({}, now=10 * day, env={})  # missing = never asked = no request
    assert not au.should_auto_check({"auto_update": "1"}, now=10 * day, env={"TLWU_OFFLINE": "1"})


def test_wizard_install_detection(tmp_path):
    from tinglongwu.gui import autoupdate as au

    assert not au.is_wizard_install(tmp_path, "win32")
    (tmp_path / "unins000.exe").write_bytes(b"")
    assert au.is_wizard_install(tmp_path, "win32")
    assert not au.is_wizard_install(tmp_path, "linux")


class FakeProc:
    def __init__(self, lines):
        self.stdout = [(l + "\n").encode() for l in lines]

    def wait(self):
        return 0


def test_run_check_and_download_events(monkeypatch):
    from tinglongwu.gui import autoupdate as au

    monkeypatch.delenv("TLWU_OFFLINE", raising=False)
    seen, cmds = [], []

    def popen(lines):
        def p(cmd, **kw):
            cmds.append(cmd)
            assert "HF_HUB_OFFLINE" not in kw["env"]
            return FakeProc(lines)
        return p

    assert au.run_check("0.3.3", seen.append, True, popen=popen(["UPDATE 0.3.4"])) == "0.3.4"
    assert cmds[0][1:] == ["-m", au.MODULE, "--check", "0.3.3"]
    au.run_check("0.3.3", seen.append, False, popen=popen(["SKIP URLError"]))
    au.run_download(seen.append, Path("d"), popen=popen(["PROGRESS 10", "PROGRESS 99", "READY C:\\t\\s.exe"]))
    au.run_download(seen.append, Path("d"), popen=popen(["FAIL ValueError: SHA256 mismatch"]))
    assert seen == [("upd_found", "0.3.4", True), ("upd_none", False), ("upd_progress", 10), ("upd_progress", 99),
                    ("upd_ready", "C:\\t\\s.exe"), ("upd_fail", "ValueError: SHA256 mismatch")]


def test_gui_side_offline_starts_no_child(monkeypatch):
    from tinglongwu.gui import autoupdate as au

    monkeypatch.setenv("TLWU_OFFLINE", "1")
    seen = []

    def boom(*a, **k):
        raise AssertionError("child started while offline")

    assert au.run_check("0.3.3", seen.append, True, popen=boom) is None
    assert au.run_download(seen.append, Path("d"), popen=boom) is None
    assert seen == [("upd_none", True), ("upd_fail", "offline")]


def test_set_auto_and_mark_checked_keep_other_keys(monkeypatch, tmp_path):
    from tinglongwu import userdirs
    from tinglongwu.gui import autoupdate as au

    monkeypatch.setenv("TLWU_CONFIG_DIR", str(tmp_path))
    userdirs.write_config_value("ui_lang", "ru")
    au.set_auto(False)
    au.mark_checked(now=123)
    assert userdirs.read_config() == {"ui_lang": "ru", "auto_update": "0", au.LAST_KEY: "123"}


# -- #946: app-layer-only update ---------------------------------------------------------------------------------
KEY = "k" * 64


def layer_release(man=None, sums_sha=SHA, size=12 << 20):
    rel = json.dumps({"tag_name": "v0.3.4", "assets": [
        {"name": n, "browser_download_url": f"https://x/{n}", "size": size if n == su.APP_ZIP_NAME else 140 << 20}
        for n in (su.EXE_NAME, su.SUMS_NAME, su.APP_ZIP_NAME, su.APP_MANIFEST_NAME)]}).encode()
    man = {"name": su.APP_ZIP_NAME, "size": size, "sha256": SHA, "url": "u", "runtime_key": KEY, "lite": False} \
        if man is None else man
    sums = f"{sums_sha}  {su.APP_ZIP_NAME}\n{'b' * 64}  {su.EXE_NAME}\n".encode()

    def get(url, timeout=su.CHECK_TIMEOUT_S):
        if url.endswith(su.APP_MANIFEST_NAME):
            if man == "boom":
                raise urllib.error.URLError("x")
            return man if isinstance(man, bytes) else json.dumps(man).encode()
        return sums if url.endswith(su.SUMS_NAME) else rel
    return get


def install_dir(tmp_path, key=KEY, lite=False):
    if key is not None:
        (tmp_path / su.RUNTIME_MANIFEST).write_text(json.dumps({"key": key, "lite": lite}))
    return tmp_path


def test_plan_app_when_runtime_key_and_lite_match(tmp_path):
    assert su.plan(install_dir(tmp_path), get=layer_release()) == "PLAN app 12"


@pytest.mark.parametrize("local,remote,expect", [
    (dict(key="z" * 64), None, "PLAN full 140"),                       # different key
    (dict(lite=True), None, "PLAN full 140"),                          # different lite
    (dict(key=None), None, "PLAN full 140"),                           # no runtime-layer.json
    (dict(), b"not json", "PLAN full 140"),                            # invalid manifest
    (dict(), {"runtime_key": KEY}, "PLAN full 140"),                   # incomplete manifest
    (dict(), "boom", "PLAN full 140"),                                 # network error on the manifest
])
def test_plan_full_fallbacks(tmp_path, local, remote, expect):
    assert su.plan(install_dir(tmp_path, **local), get=layer_release(man=remote)) == expect


def test_plan_full_on_network_error(tmp_path):
    def get(url, timeout=0):
        raise urllib.error.URLError("down")
    assert su.plan(install_dir(tmp_path), get=get).startswith("PLAN full")


def test_download_app_checks_both_sums(tmp_path):
    got = []

    def fetch(url, dest, sha, on_bytes=None):
        got.append((url, dest.name, sha))
        return dest
    p = su.download_app(tmp_path, get=layer_release(), fetch=fetch)
    assert got == [(f"https://x/{su.APP_ZIP_NAME}", "tinglongwu-app-layer-0.3.4.zip", SHA)] and p.name.endswith(".zip")
    with pytest.raises(ValueError):  # SHA256SUMS.txt and the manifest disagree
        su.download_app(tmp_path, get=layer_release(sums_sha="c" * 64), fetch=fetch)


def test_download_app_sha_mismatch_deletes_file_and_fails(tmp_path, monkeypatch, capsys):
    def fetch(url, dest, sha, on_bytes=None):
        dest.write_bytes(b"bad")
        raise ValueError("SHA256 mismatch")
    with pytest.raises(ValueError):
        su.download_app(tmp_path, get=layer_release(), fetch=fetch)
    assert not list(tmp_path.glob("*.zip"))
    monkeypatch.delenv("TLWU_OFFLINE", raising=False)
    monkeypatch.setattr(su, "download_app", lambda d: fetch("u", d / "a.zip", SHA))
    assert su.main(["--download-app", str(tmp_path)]) == 1
    assert capsys.readouterr().out.startswith("FAIL ValueError")


def test_apply_app_script_has_rollback_and_stays_in_app_dir():
    app = Path(r"C:\Users\a\it's\tinglongWu")
    ps = su.apply_app_script(Path(r"C:\T\app.zip"), app, 99)
    assert r"$app = 'C:\Users\a\it''s\tinglongWu'" in ps and "Wait-Process -Id 99" in ps
    assert "} catch {" in ps and "-NewName $i" in ps  # rollback puts *.old back
    for item in ("tinglongwu", "tlwu_app", "tinglongwu_setup", "app-layer.json", "tlwu.exe"):
        assert f"'{item}'" in ps
    assert "models" not in ps and "'.update-staging'" in ps
    import re
    assert set(re.findall(r"[A-Za-z]:\\[^']*", ps)) <= {r"C:\Users\a\it", r"C:\T\app.zip"}  # app dir + the zip only


def test_apply_app_script_records_item_only_after_rename_succeeds():
    ps = su.apply_app_script(Path(r"C:\T\app.zip"), Path(r"C:\A"), 1)
    lines = ps.splitlines()
    idx = lambda needle: next(n for n, ln in enumerate(lines) if needle in ln)  # noqa: E731
    # a failing Rename-Item (locked file) must leave $moved without that item, so the catch never deletes the original
    assert idx("Rename-Item -LiteralPath $cur") < idx("$moved += $i") < idx("Move-Item")


def test_apply_app_cli_uses_app_script(tmp_path, monkeypatch):
    calls = []
    su.launch_apply(tmp_path / "a.zip", tmp_path, 5, popen=lambda cmd, **kw: calls.append(cmd), app_layer=True)
    assert "tlwu_app" in (tmp_path / "tlwu-apply-update.ps1").read_text("utf-8-sig") and calls


def test_gui_logs_which_update_and_falls_back(monkeypatch, tmp_path):
    from tinglongwu.gui import autoupdate as au

    monkeypatch.delenv("TLWU_OFFLINE", raising=False)
    logs, seen, cmds = [], [], []
    monkeypatch.setattr(au, "_log", logs.append)

    def popen(plan_lines, app_lines):
        def p(cmd, **kw):
            cmds.append(cmd)
            if "--plan" in cmd:
                return FakeProc(plan_lines)
            return FakeProc(app_lines if "--download-app" in cmd else ["READY C:/t/setup.exe"])
        return p

    r = au.run_download(seen.append, tmp_path, popen=popen(["PLAN app 12"], ["PROGRESS 50", "READY C:/t/a.zip"]))
    assert r == "C:/t/a.zip" and logs == ["update: app layer only: 12 MB"] and ("upd_ready", "C:/t/a.zip") in seen
    logs.clear()
    r = au.run_download(seen.append, tmp_path, popen=popen(["PLAN full 140"], []))
    assert r == "C:/t/setup.exe" and logs == ["update: full installer: 140 MB"]
    logs.clear()  # app-layer download fails -> full installer
    r = au.run_download(seen.append, tmp_path, popen=popen(["PLAN app 12"], ["FAIL ValueError: x"]))
    assert r == "C:/t/setup.exe" and logs[-1] == "update: full installer: 12 MB"
    assert au.apply_cmd("C:/t/a.zip", tmp_path, 1)[-6] == "--apply-app" and "--apply" in au.apply_cmd("s.exe", tmp_path, 1)


def test_cleanup_old_removes_leftovers(tmp_path):
    from tinglongwu.gui import autoupdate as au

    (tmp_path / "tinglongwu.old").mkdir()
    (tmp_path / "tinglongwu.old" / "x.py").write_text("1")
    (tmp_path / "tlwu.exe.old").write_text("1")
    (tmp_path / "models").mkdir()
    au.cleanup_old(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["models"]
