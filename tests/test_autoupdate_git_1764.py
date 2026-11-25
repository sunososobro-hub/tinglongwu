"""#1764: git-checkout update reports changed deps; the failed-check recovery URL is not the Windows .exe."""
import subprocess

from tinglongwu.gui import autoupdate


def _fake_git(changed):
    def run(cmd, **kw):
        a = cmd[1:]
        out = ""
        if a[:1] == ["rev-parse"] and "--abbrev-ref" in a:
            out = "origin/main\n"
        elif a[:1] == ["rev-parse"]:
            out = "abc123\n"
        elif a[:1] == ["diff"]:
            out = "\n".join(changed) + "\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return run


def test_git_apply_reports_deps_changed_for_pyproject(tmp_path):
    assert autoupdate.git_apply(tmp_path, _fake_git(["README.md", "pyproject.toml"])) is True
    assert autoupdate.git_apply(tmp_path, _fake_git(["requirements/base.txt"])) is True


def test_git_apply_no_deps_change(tmp_path):
    assert autoupdate.git_apply(tmp_path, _fake_git(["src/x.py", "docs/a.md"])) is False


def test_recovery_url_is_not_the_exe_for_a_git_checkout(tmp_path):
    (tmp_path / ".git").mkdir()
    for plat in ("linux", "darwin"):
        url = autoupdate.recovery_url(tmp_path, plat)
        assert not url.endswith(".exe") and url == autoupdate.RELEASES_URL


def test_recovery_url_keeps_the_exe_for_a_windows_wizard_install(tmp_path):
    (tmp_path / "unins000.exe").write_bytes(b"")
    assert autoupdate.recovery_url(tmp_path, "win32") == autoupdate.DOWNLOAD_URL
