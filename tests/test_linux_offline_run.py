"""scripts/linux_offline_run.sh (#258): --dry-run command assembly, env passing, fallback order."""
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import firewall_test as ft

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "linux_offline_run.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH or sys.platform == "win32", reason="needs bash")


def run(tmp_path, tools, *args, **extra_env):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for t in ("id", *tools):
        stub = bindir / t
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    env = {"PATH": str(bindir), "HOME": "/home/u", "XDG_RUNTIME_DIR": "/run/user/1000",
           "TLWU_UID": "1000", "TLWU_GID": "1000", **extra_env}
    script = tmp_path / "repo" / "scripts" / SCRIPT.name  # a copy, so a developer's repo .venv can't leak in
    if not script.exists():
        script.parent.mkdir(parents=True)
        shutil.copy2(SCRIPT, script)
    return subprocess.run([BASH, str(script), "--dry-run", *args], capture_output=True, text=True, env=env)


def test_primary_path_runs_as_user_with_env(tmp_path):
    r = run(tmp_path, ["sudo", "unshare", "setpriv", "firejail"], "echo", "hi")
    assert r.returncode == 0
    out = r.stdout
    assert shlex.split(out)[:5] == ["sudo", "unshare", "-n", "--", "sh"]
    assert "ip link set lo up" in shlex.split(out)[6]
    for want in ("--reuid=1000", "--regid=1000", "--init-groups", "HOME=/home/u",
                 "XDG_RUNTIME_DIR=/run/user/1000", "TLWU_NO_UPDATE=1"):
        assert want in out
    assert out.strip().endswith("echo hi")
    assert "firejail" not in out


def test_falls_back_to_firejail(tmp_path):
    r = run(tmp_path, ["firejail", "unshare"], "echo", "hi")  # no sudo/setpriv
    assert r.returncode == 0
    assert r.stdout.startswith("firejail --noprofile --net=none")
    assert "TLWU_NO_UPDATE=1" in r.stdout


def test_no_tool_exits_nonzero_with_guidance(tmp_path):
    r = run(tmp_path, [], "echo")
    assert r.returncode != 0
    assert "firejail" in r.stderr and "util-linux" in r.stderr


def test_default_command_is_tlwu(tmp_path):
    r = run(tmp_path, ["sudo", "unshare", "setpriv", "python3"])
    assert shlex.split(r.stdout)[-3:] == [str(tmp_path / "bin" / "python3"), "-m", "tinglongwu"]


def test_repo_venv_python_is_used_when_no_venv_is_active(tmp_path):
    py = tmp_path / "repo" / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\nexit 0\n")
    py.chmod(0o755)
    r = run(tmp_path, ["sudo", "unshare", "setpriv", "python3"], "--file", "talk.wav")
    assert shlex.split(r.stdout)[-5:] == [str(py), "-m", "tinglongwu", "--file", "talk.wav"]


def test_firewall_test_prints_alternatives_on_uid_map_error():
    def fake_run(cmd, **kw):
        return SimpleNamespace(returncode=1, stdout="", stderr="unshare: write failed /proc/self/uid_map: Operation not permitted")
    with pytest.raises(ft.SetupError) as e:
        ft.probe(ft.NamespaceBlock(), fake_run)
    msg = str(e.value)
    assert "linux_offline_run.sh" in msg and "AppArmor" in msg and "firejail" in msg


def _venv(tmp_path):
    py = tmp_path / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\nexit 0\n")
    py.chmod(0o755)
    return py


def test_leading_option_runs_tlwu_with_absolute_venv_python(tmp_path):
    # the documented `linux_offline_run.sh --file talk.wav`: `--file` is a tlwu option, not the command,
    # and python is absolute because sudo's secure_path drops the venv from PATH
    py = _venv(tmp_path)
    r = run(tmp_path, ["sudo", "unshare", "setpriv"], "--file", "talk.wav", VIRTUAL_ENV=str(py.parent.parent))
    assert r.returncode == 0, r.stderr
    argv = shlex.split(r.stdout)
    assert argv[-5:] == [str(py), "-m", "tinglongwu", "--file", "talk.wav"]


def test_leading_option_without_venv_uses_absolute_python_from_path(tmp_path):
    r = run(tmp_path, ["sudo", "unshare", "setpriv", "python3"], "--file", "talk.wav")
    assert r.returncode == 0, r.stderr
    argv = shlex.split(r.stdout)
    assert argv[-5:] == [str(tmp_path / "bin" / "python3"), "-m", "tinglongwu", "--file", "talk.wav"]


def test_named_command_is_resolved_to_absolute_path(tmp_path):
    r = run(tmp_path, ["sudo", "unshare", "setpriv", "tlwu"], "tlwu", "--lang", "en")
    assert r.returncode == 0, r.stderr
    assert shlex.split(r.stdout)[-3:] == [str(tmp_path / "bin" / "tlwu"), "--lang", "en"]


def test_firejail_path_also_takes_leading_options(tmp_path):
    r = run(tmp_path, ["firejail", "python3"], "--file", "talk.wav")
    assert r.returncode == 0, r.stderr
    assert shlex.split(r.stdout)[-5:] == [str(tmp_path / "bin" / "python3"), "-m", "tinglongwu", "--file", "talk.wav"]
