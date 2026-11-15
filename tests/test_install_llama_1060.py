"""#1060: scripts/install.sh (Linux) installs llama-cpp-python, the Hy-MT2 engine every default `tlwu` run needs.

Before the fix a clean Linux install had no llama_cpp, so `tlwu --file clip` stopped at "Hy-MT2 failed to load" and the
install-clean ttfs job never saw a subtitle. These tests run the real install_llama function with a fake .venv python."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="needs bash")

FAKE_PY = """#!/bin/bash
echo "$*" >> "$LOG"
if [ "$1" = "-c" ]; then exit "${IMPORT_RC:-1}"; fi
exit "${PIP_RC:-0}"
"""


def _run(tmp_path, **env):
    d = tmp_path / "app"
    (d / "scripts").mkdir(parents=True)
    (d / ".venv" / "bin").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "tlwu_common.sh", d / "scripts" / "tlwu_common.sh")
    shutil.copy(ROOT / "requirements-llama-cpu.txt", d / "requirements-llama-cpu.txt")
    py = d / ".venv" / "bin" / "python"
    py.write_text(FAKE_PY)
    py.chmod(0o755)
    log = tmp_path / "calls.log"
    log.write_text("")
    code = 'TLWU_SOURCE_ONLY=1 source "$S"; DIR="$D"; cd "$D"; L=en; install_llama'
    r = subprocess.run(["bash", "-c", code], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "S": str(ROOT / "scripts" / "install.sh"),
                            "D": str(d), "LOG": str(log), **env})
    return r, log.read_text().splitlines()


def test_missing_llama_is_installed_from_the_cpu_requirements(tmp_path):
    r, calls = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "-m pip install -r requirements-llama-cpu.txt" in calls
    assert "Hy-MT2" in r.stdout


def test_working_llama_is_left_alone(tmp_path):
    r, calls = _run(tmp_path, IMPORT_RC="0")
    assert r.returncode == 0, r.stderr
    assert not [c for c in calls if "pip" in c]


def test_failed_install_warns_that_translation_will_not_work(tmp_path):
    r, calls = _run(tmp_path, PIP_RC="1")
    assert r.returncode == 1
    assert "-m pip install --no-binary llama-cpp-python llama-cpp-python==0.3.36" in calls   # #420 sdist fallback
    assert "cannot translate" in r.stderr
