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


def run(tmp_path, tools, *args):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for t in ("id", *tools):
        stub = bindir / t
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    env = {"PATH": str(bindir), "HOME": "/home/u", "XDG_RUNTIME_DIR": "/run/user/1000",
           "TLWU_UID": "1000", "TLWU_GID": "1000"}
    return subprocess.run([BASH, str(SCRIPT), "--dry-run", *args], capture_output=True, text=True, env=env)


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
    r = run(tmp_path, ["sudo", "unshare", "setpriv"])
    assert "python -m tinglongwu" in r.stdout


def test_firewall_test_prints_alternatives_on_uid_map_error():
    def fake_run(cmd, **kw):
        return SimpleNamespace(returncode=1, stdout="", stderr="unshare: write failed /proc/self/uid_map: Operation not permitted")
    with pytest.raises(ft.SetupError) as e:
        ft.probe(ft.NamespaceBlock(), fake_run)
    msg = str(e.value)
    assert "linux_offline_run.sh" in msg and "AppArmor" in msg and "firejail" in msg
