"""#1738: mobile-apk on the self-hosted Windows runner must run its bash steps in Git Bash, not the WSL stub.

scripts/windows_git_bash.py finds Git for Windows' bin dir and appends it to GITHUB_PATH; the build job runs it
(Windows only, under cmd) before its first bash step."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PureWindowsPath

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import windows_git_bash as wgb  # noqa: E402

yaml = pytest.importorskip("yaml")


def _fs(*files):
    have = {str(PureWindowsPath(f)).lower() for f in files}
    return lambda p: str(PureWindowsPath(p)).lower() in have


def test_git_on_path_wins(tmp_path):
    out = tmp_path / "gh_path"
    env = {"GITHUB_PATH": str(out), "ProgramFiles": r"C:\Program Files"}
    rc = wgb.main(env, r"D:\Tools\Git\cmd\git.exe", _fs(r"D:\Tools\Git\bin\bash.exe", r"C:\Program Files\Git\bin\bash.exe"))
    assert rc == 0
    assert out.read_text().splitlines() == [r"D:\Tools\Git\bin"]


def test_program_files_fallback_when_git_not_on_path(tmp_path):
    out = tmp_path / "gh_path"
    env = {"GITHUB_PATH": str(out), "ProgramFiles": r"C:\Program Files"}
    assert wgb.main(env, "", _fs(r"C:\Program Files\Git\bin\bash.exe")) == 0
    assert out.read_text().strip() == r"C:\Program Files\Git\bin"


def test_user_install_under_localappdata(tmp_path):
    out = tmp_path / "gh_path"
    env = {"GITHUB_PATH": str(out), "LOCALAPPDATA": r"C:\Users\user\AppData\Local"}
    assert wgb.main(env, "", _fs(r"C:\Users\user\AppData\Local\Programs\Git\bin\bash.exe")) == 0
    assert out.read_text().strip() == r"C:\Users\user\AppData\Local\Programs\Git\bin"


def test_wsl_stub_is_never_taken(tmp_path):
    out = tmp_path / "gh_path"
    stub_git = r"C:\Users\user\AppData\Local\Microsoft\WindowsApps\git.exe"
    env = {"GITHUB_PATH": str(out)}
    assert wgb.main(env, stub_git, _fs(r"C:\Users\user\AppData\Local\Microsoft\WindowsApps\bash.exe")) == 1
    assert not out.exists()


def test_missing_git_bash_fails_with_message(tmp_path, capsys):
    assert wgb.main({"GITHUB_PATH": str(tmp_path / "p"), "ProgramFiles": r"C:\Program Files"}, "", _fs()) == 1
    assert "Git for Windows bash.exe not found" in capsys.readouterr().err


def test_cli_runs_and_exits_1_without_git_bash(tmp_path):
    # Linux CI: no Windows Git, so the real script must exit 1 and leave GITHUB_PATH untouched
    out = tmp_path / "gh_path"
    r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/windows_git_bash.py")], capture_output=True, text=True,
                       env={"GITHUB_PATH": str(out), "PATH": "/nonexistent"})
    assert r.returncode == 1 and not out.exists()


def _build_steps():
    wf = yaml.safe_load((ROOT / ".github/workflows/mobile-apk.yml").read_text(encoding="utf-8"))
    job = wf["jobs"]["build"]
    return job, job["steps"]


def test_git_bash_step_runs_before_every_bash_step():
    job, steps = _build_steps()
    assert job["defaults"]["run"]["shell"] == "bash"
    idx = next(i for i, s in enumerate(steps) if "windows_git_bash.py" in (s.get("run") or ""))
    prep = steps[idx]
    assert prep["if"] == "runner.os == 'Windows'" and prep["shell"] == "cmd" and prep["working-directory"] == "."
    first_bash = next(i for i, s in enumerate(steps) if "run" in s and s.get("shell", "bash") == "bash")
    assert idx < first_bash
    # the script path in the step exists once checked out (checkout comes first)
    assert any("actions/checkout" in (s.get("uses") or "") for s in steps[:idx])
    script = prep["run"].split()[-1].replace("\\", "/")
    assert (ROOT / script).is_file()
