"""#1857: scripts/ci_parallel.sh runs the normal and offline pytest passes at once; `test` must still fail when either fails."""
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ci_parallel.sh"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="bash CI helper (ubuntu runner)")


def _run(*args, tmp_path):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=60,
                          env={"PATH": "/usr/bin:/bin", "RUNNER_TEMP": str(tmp_path)})


def test_all_pass_exits_zero_and_prefixes_output(tmp_path):
    r = _run("normal", "echo hi", "offline", "echo there", tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[normal] hi" in r.stdout and "[offline] there" in r.stdout
    assert "[ci_parallel] normal exit 0" in r.stdout and "[ci_parallel] offline exit 0" in r.stdout


@pytest.mark.parametrize("first,second", [("exit 3", "true"), ("true", "exit 4"), ("exit 1", "exit 1")])
def test_any_failing_command_fails_the_run(tmp_path, first, second):
    r = _run("normal", first, "offline", second, tmp_path=tmp_path)
    assert r.returncode == 1, r.stdout


def test_commands_run_at_the_same_time(tmp_path):
    t = time.monotonic()
    r = _run("a", "sleep 2", "b", "sleep 2", tmp_path=tmp_path)
    assert r.returncode == 0
    assert time.monotonic() - t < 3.5  # serial would take >= 4 s


def test_each_command_gets_its_own_tmpdir(tmp_path):
    r = _run("a", 'echo "$TMPDIR"', "b", 'echo "$TMPDIR"', tmp_path=tmp_path)
    a = [ln.split(" ", 1)[1] for ln in r.stdout.splitlines() if ln.startswith("[a] ")]
    b = [ln.split(" ", 1)[1] for ln in r.stdout.splitlines() if ln.startswith("[b] ")]
    assert a and b and a != b
    assert Path(a[0]).is_dir() and Path(b[0]).is_dir()
    assert Path(a[0]).is_relative_to(tmp_path)


def test_bad_usage_exits_2(tmp_path):
    assert _run("only-a-label", tmp_path=tmp_path).returncode == 2
