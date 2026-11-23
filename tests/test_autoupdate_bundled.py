"""#768: auto-update from a prebuilt-core install (tlwu.exe, no `python -m`)."""
import subprocess
import sys
from pathlib import Path

import pytest

from tinglongwu.gui import autoupdate
from tlwu_app import main as app_main

EXE = str(Path("C:/Programs/tinglongWu/tlwu.exe"))


@pytest.fixture
def bundled(monkeypatch, tmp_path):
    exe = tmp_path / "tlwu.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("TLWU_BUNDLED", "1")
    monkeypatch.setattr(sys, "executable", str(exe))
    return exe


@pytest.fixture
def source(monkeypatch):
    monkeypatch.delenv("TLWU_BUNDLED", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)


def test_bundled_semantics_match_the_launcher(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    for env in ("1", None):
        if env:
            monkeypatch.setenv("TLWU_BUNDLED", env)
        else:
            monkeypatch.delenv("TLWU_BUNDLED", raising=False)
        assert autoupdate.is_bundled() == app_main.is_bundled() == bool(env)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert autoupdate.is_bundled() and app_main.is_bundled()


def test_bundled_cmds_rerun_tlwu_exe(bundled, tmp_path):
    exe = str(bundled)
    assert autoupdate.check_cmd("0.3.2") == [exe, "--run-selfupdate", "--check", "0.3.2"]
    assert autoupdate.download_cmd(tmp_path) == [exe, "--run-selfupdate", "--download", str(tmp_path)]
    assert autoupdate.apply_cmd("s.exe", tmp_path, 9) == [exe, "--run-selfupdate", "--apply", "s.exe",
                                                          "--app", str(tmp_path), "--pid", "9"]
    assert "-m" not in autoupdate.check_cmd("0.3.2")


def test_source_cmds_keep_python_m(source):
    assert autoupdate.check_cmd("0.3.2", python="py") == ["py", "-m", "tinglongwu_setup.selfupdate", "--check", "0.3.2"]
    assert autoupdate.app_root() == Path(autoupdate.__file__).resolve().parents[4]


def test_bundled_app_root_is_the_exe_folder_and_counts_as_wizard_install(bundled):
    assert autoupdate.app_root() == bundled.parent
    assert not autoupdate.is_wizard_install(platform="win32")
    (bundled.parent / "unins000.exe").write_bytes(b"")
    assert autoupdate.is_wizard_install(platform="win32")
    assert not autoupdate.is_wizard_install(platform="linux")


def test_start_apply_bundled_passes_exe_folder(bundled):
    calls = []

    class P:
        def wait(self):
            return 0

    autoupdate.start_apply("setup.exe", pid=5, popen=lambda cmd, **kw: calls.append(cmd) or P())
    assert calls[0][:2] == [str(bundled), "--run-selfupdate"]
    assert calls[0][calls[0].index("--app") + 1] == str(bundled.parent)


def test_run_selfupdate_routes_to_selfupdate_main(monkeypatch):
    from tinglongwu_setup import selfupdate

    seen = []
    monkeypatch.setattr(selfupdate, "main", lambda args: seen.append(args) or 0)
    assert app_main.main(["--run-selfupdate", "--check", "0.3.2"]) == 0
    assert seen == [["--check", "0.3.2"]]


def test_run_selfupdate_child_never_imports_tinglongwu(tmp_path):
    """Real child process: the launcher route reaches selfupdate.main without importing the runtime package."""
    root = Path(__file__).resolve().parent.parent
    code = (
        "import sys\n"
        "from tinglongwu_setup import selfupdate\n"
        "selfupdate.main = lambda a: print('MAIN', a) or 0\n"
        "from tlwu_app import main as m\n"
        "rc = m.main(['--run-selfupdate', '--check', '0.0.1'])\n"
        "bad = sorted(k for k in sys.modules if k == 'tinglongwu' or k.startswith('tinglongwu.'))\n"
        "print('RC', rc, 'BAD', bad)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "MAIN ['--check', '0.0.1']" in r.stdout
    assert "RC 0 BAD []" in r.stdout


def test_apply_script_relaunches_tlwu_exe_first():
    from tinglongwu_setup import selfupdate

    ps = selfupdate.apply_script(Path("C:/T/setup.exe"), Path("C:/Programs/tinglongWu"), 1)
    i_core, i_venv = ps.index("tlwu.exe'"), ps.index("tlwu-gui.exe")
    assert i_core < i_venv


# --- #1672: Linux / git-checkout macOS update check follows the marker tag, like scripts/auto_update.sh ---------------
def _git_repo(tmp_path):
    def g(*a, cwd):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True)

    origin, work = tmp_path / "origin", tmp_path / "work"
    origin.mkdir()
    g("init", "-q", "-b", "main", cwd=origin)
    (origin / "f").write_text("1")
    g("add", ".", cwd=origin)
    g("commit", "-q", "-m", "one", cwd=origin)
    g("tag", "linux-ok", cwd=origin)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-q", "--detach", "linux-ok"], cwd=work, check=True, capture_output=True)
    return g, origin, work


def test_git_checkout_update_available_and_apply(source, monkeypatch, tmp_path):
    monkeypatch.delenv("TLWU_OFFLINE", raising=False)
    g, origin, work = _git_repo(tmp_path)
    assert autoupdate.is_git_checkout(work, "linux") and autoupdate.can_update(work, "linux")
    assert not autoupdate.is_git_checkout(work, "win32")
    assert autoupdate.git_check(work, platform="linux") == "uptodate"
    (origin / "f").write_text("2")
    g("commit", "-q", "-am", "two", cwd=origin)
    g("tag", "-f", "linux-ok", cwd=origin)  # the release moved the marker tag
    assert autoupdate.git_check(work, platform="linux") == "update"
    events = []
    assert autoupdate.run_git_check(events.append, True, "0.0.0", root=work) == "latest"
    assert events == [("upd_found", "latest", True)]
    autoupdate.git_apply(work, platform="linux")
    assert (work / "f").read_text() == "2"
    assert autoupdate.git_check(work, platform="linux") == "uptodate"


def test_git_check_ignores_commits_past_the_marker_tag(source, monkeypatch, tmp_path):
    monkeypatch.delenv("TLWU_OFFLINE", raising=False)
    g, origin, work = _git_repo(tmp_path)
    (origin / "f").write_text("3")
    g("commit", "-q", "-am", "unreleased", cwd=origin)  # main moved, marker tag did not
    assert autoupdate.git_check(work, platform="linux") == "uptodate"


def test_git_check_fetch_failure_is_reported(source, monkeypatch, tmp_path):
    monkeypatch.delenv("TLWU_OFFLINE", raising=False)
    g, origin, work = _git_repo(tmp_path)
    subprocess.run(["git", "remote", "set-url", "origin", str(tmp_path / "gone")], cwd=work, check=True)
    events = []
    assert autoupdate.run_git_check(events.append, True, "0.0.0", root=work) is None
    assert events[0][0] == "upd_fail_check"


def test_git_apply_reports_dependency_changes(source, monkeypatch, tmp_path):
    monkeypatch.delenv("TLWU_OFFLINE", raising=False)
    g, origin, work = _git_repo(tmp_path)
    (origin / "f").write_text("2")
    g("commit", "-q", "-am", "plain", cwd=origin)
    g("tag", "-f", "linux-ok", cwd=origin)
    assert autoupdate.git_apply(work, platform="linux") is False
    (origin / "pyproject.toml").write_text("[project]\n")
    g("add", ".", cwd=origin)
    g("commit", "-q", "-m", "deps", cwd=origin)
    g("tag", "-f", "linux-ok", cwd=origin)
    events = []
    assert autoupdate.git_check(work, platform="linux") == "update"
    assert autoupdate.run_git_apply(events.append, root=work) is True
    assert events == [("upd_git_done", True)]


def test_git_check_unresolvable_target_is_an_error(source, monkeypatch, tmp_path):
    monkeypatch.delenv("TLWU_OFFLINE", raising=False)
    g, origin, work = _git_repo(tmp_path)
    subprocess.run(["git", "tag", "-d", "linux-ok"], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "update-ref", "-d", "refs/remotes/origin/main"], cwd=work, check=True, capture_output=True)
    events = []
    # fetch --tags restores linux-ok, so point the target at a name that cannot resolve
    monkeypatch.setattr(autoupdate, "git_target", lambda *a, **k: "refs/tags/nope")
    assert autoupdate.run_git_check(events.append, True, "0.0.0", root=work) is None
    assert events[0][0] == "upd_fail_check"
