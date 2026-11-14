"""#698: Windows auto-update. Network side (tinglongwu_setup.selfupdate) and GUI side (tinglongwu.gui.autoupdate),
all offline: every request is a fake."""
import io
import json
import socket
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


@pytest.mark.parametrize("exc", [socket.timeout("timed out"), TimeoutError(),
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
    assert "tlwu-gui.exe" in lines[3] and lines.index(lines[3]) > lines.index(lines[2])


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
