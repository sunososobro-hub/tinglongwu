"""#420: tlwu_install_llama falls back to the PyPI sdist when the index wheel install fails. Runs the real shell
function against a stub python that records its pip argv."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMMON_SH = ROOT / "scripts" / "tlwu_common.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")
# #1356: these tests cover the index-wheel / sdist path; the prebuilt wheel (a download on Apple Silicon) is covered by
# tests/test_install_llama_prebuilt.py and switched off here so a Mac runs them without the network.
NO_PREBUILT = {"TLWU_LLAMA_NO_PREBUILT": "1"}

STUB = """#!/bin/bash
echo "$@" >> "$STUB_LOG"
echo "jobs=${CMAKE_BUILD_PARALLEL_LEVEL:-unset} cmake=[${CMAKE_ARGS:-unset}] $*" >> "$STUB_LOG.env"
sleep "${STUB_SLEEP:-0}"
echo "pip says: $*"
case "$*" in *--no-binary*) exit "${SDIST_RC:-0}";; *) exit "${WHEEL_RC:-0}";; esac
"""


def _run(tmp_path, wheel_rc, sdist_rc=0):
    py = tmp_path / "py"
    py.write_text(STUB)
    py.chmod(0o755)
    req = tmp_path / "req.txt"
    req.write_text("--extra-index-url https://example.invalid/whl/cpu\nllama-cpp-python==0.3.36\n")
    log = tmp_path / "log"
    r = subprocess.run(
        [BASH, "-c", f'. "{COMMON_SH}"; tlwu_install_llama "{py}" "{req}"'],
        env={"PATH": "/usr/bin:/bin", "STUB_LOG": str(log), "WHEEL_RC": str(wheel_rc), "SDIST_RC": str(sdist_rc), **NO_PREBUILT},
        capture_output=True, text=True,
    )
    return r.returncode, log.read_text().splitlines()


def test_wheel_ok_no_fallback(tmp_path):
    rc, calls = _run(tmp_path, 0)
    assert rc == 0 and len(calls) == 1 and "-r" in calls[0]


def test_wheel_fails_builds_sdist(tmp_path):
    rc, calls = _run(tmp_path, 1)
    assert rc == 0 and len(calls) == 2
    assert "--no-binary llama-cpp-python llama-cpp-python==0.3.36" in calls[1]


def test_both_fail_returns_nonzero(tmp_path):
    rc, _ = _run(tmp_path, 1, 1)
    assert rc != 0


def _sh(tmp_path, script, **env):
    py = tmp_path / "py"
    py.write_text(STUB)
    py.chmod(0o755)
    req = tmp_path / "req.txt"
    req.write_text("llama-cpp-python==0.3.36\n")
    log = tmp_path / "log"
    r = subprocess.run([BASH, "-c", f'. "{COMMON_SH}"; PY="{py}"; REQ="{req}"; OUT="{tmp_path}/bg.log"; {script}'],
                       env={"PATH": "/usr/bin:/bin", "STUB_LOG": str(log), **NO_PREBUILT, **env}, capture_output=True,
                       text=True)
    envlog = Path(str(log) + ".env")
    return r, envlog.read_text().splitlines() if envlog.exists() else []


def test_sdist_build_uses_every_core_1341(tmp_path):
    # #1341: make compiles one file at a time unless CMAKE_BUILD_PARALLEL_LEVEL is set; the wheel attempt gets none
    r, env = _sh(tmp_path, 'tlwu_install_llama "$PY" "$REQ"; echo "ncpu=$(tlwu_ncpu)"', WHEEL_RC="1")
    ncpu = int(r.stdout.strip().splitlines()[-1].split("=")[1])
    assert ncpu >= 1 and env[0].startswith("jobs=unset ") and env[1].startswith(f"jobs={ncpu} ") and "--no-binary" in env[1]


def test_sdist_build_keeps_a_parallel_level_the_user_set_1341(tmp_path):
    _, env = _sh(tmp_path, 'tlwu_install_llama "$PY" "$REQ"', WHEEL_RC="1", CMAKE_BUILD_PARALLEL_LEVEL="7")
    assert env[1].startswith("jobs=7 ")


def test_background_install_runs_while_the_caller_works_1341(tmp_path):
    # the install sleeps 1 s in the background; the caller's own line comes first, then wait prints the pip output
    r, _ = _sh(tmp_path, 'tlwu_install_llama_bg "$PY" "$REQ" "$OUT"; echo "caller works"; '
                         'tlwu_wait_llama "$OUT"; echo "rc=$? pid=[$TLWU_LLAMA_PID] log=$(ls "$OUT" 2>/dev/null)"',
               STUB_SLEEP="1")
    lines = r.stdout.splitlines()
    assert lines[0] == "caller works" and lines[1].startswith("pip says: -m pip install -r")
    assert "llama-cpp-python: " in lines[2] and "(exit 0)" in lines[2]
    assert lines[-1] == "rc=0 pid=[] log="  # exit code passed on, pid cleared, log file removed


def test_background_install_failure_reaches_the_caller_1341(tmp_path):
    r, _ = _sh(tmp_path, 'tlwu_install_llama_bg "$PY" "$REQ" "$OUT"; tlwu_wait_llama "$OUT"; echo "rc=$?"; '
                         'tlwu_wait_llama "$OUT"; echo "again=$?"', WHEEL_RC="1", SDIST_RC="3")
    out = r.stdout.splitlines()
    assert "rc=3" in out and out[-1] == "again=1" and any("building llama-cpp-python==0.3.36" in ln for ln in out)


def test_sdist_build_trims_cmake_targets(tmp_path):
    _run(tmp_path, 1)
    env_lines = (tmp_path / "log.env").read_text().splitlines()
    assert "-DLLAVA_BUILD=OFF" in env_lines[1] and "-DLLAMA_BUILD_SERVER=OFF" in env_lines[1]
    assert "cmake=[unset]" in env_lines[0]  # the index-wheel attempt is not touched
