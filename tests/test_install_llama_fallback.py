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

STUB = """#!/bin/bash
echo "$@" >> "$STUB_LOG"
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
        env={"PATH": "/usr/bin:/bin", "STUB_LOG": str(log), "WHEEL_RC": str(wheel_rc), "SDIST_RC": str(sdist_rc)},
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
