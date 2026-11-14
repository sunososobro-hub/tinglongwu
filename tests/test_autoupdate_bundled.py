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
    assert autoupdate.app_root() == Path(autoupdate.__file__).resolve().parents[2]


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
