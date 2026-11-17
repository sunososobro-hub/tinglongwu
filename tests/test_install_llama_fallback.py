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
case "$*" in
  *--no-binary*) exit "${SDIST_RC:-0}";;
  *whl/metal*) exit "${METAL_RC:-0}";;
  "-c import llama_cpp") exit "${IMPORT_RC:-0}";;
  *) exit "${WHEEL_RC:-0}";;
esac
"""
UNAME = """#!/bin/bash
case "$1" in -s) echo "$FAKE_OS";; -m) echo "$FAKE_ARCH";; esac
"""


def _run(tmp_path, wheel_rc, sdist_rc=0, uname=None, metal_rc=0, import_rc=0):
    py = tmp_path / "py"
    py.write_text(STUB)
    py.chmod(0o755)
    req = tmp_path / "req.txt"
    req.write_text("--extra-index-url https://example.invalid/whl/cpu\nllama-cpp-python==0.3.36\n")
    log = tmp_path / "log"
    env = {"PATH": "/usr/bin:/bin", "STUB_LOG": str(log), "WHEEL_RC": str(wheel_rc), "SDIST_RC": str(sdist_rc),
           "METAL_RC": str(metal_rc), "IMPORT_RC": str(import_rc)}
    if uname:  # (system, machine): a fake `uname` first on PATH, e.g. ("Darwin", "arm64")
        fake = tmp_path / "bin"
        fake.mkdir()
        (fake / "uname").write_text(UNAME)
        (fake / "uname").chmod(0o755)
        env.update(PATH=f"{fake}:/usr/bin:/bin", FAKE_OS=uname[0], FAKE_ARCH=uname[1])
    r = subprocess.run(
        [BASH, "-c", f'. "{COMMON_SH}"; tlwu_install_llama "{py}" "{req}"'],
        env=env, capture_output=True, text=True,
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


MAC = ("Darwin", "arm64")


def test_apple_silicon_takes_prebuilt_metal_wheel_no_build_1341(tmp_path):
    # #1341: binary only, the pinned version, from the Metal index; then the import check; nothing else
    rc, calls = _run(tmp_path, 1, 1, uname=MAC)
    assert rc == 0 and len(calls) == 2
    assert "--only-binary=:all:" in calls[0] and calls[0].endswith("/whl/metal llama-cpp-python==0.3.36")
    assert calls[1] == "-c import llama_cpp"


def test_apple_silicon_wheel_missing_falls_back_to_old_path_1341(tmp_path):
    rc, calls = _run(tmp_path, 1, uname=MAC, metal_rc=1)
    assert rc == 0 and "-r" in calls[-2] and "--no-binary llama-cpp-python llama-cpp-python==0.3.36" in calls[-1]


def test_apple_silicon_wheel_not_importable_is_removed_then_old_path_1341(tmp_path):
    rc, calls = _run(tmp_path, 0, uname=MAC, import_rc=1)
    assert rc == 0 and any("uninstall -y llama-cpp-python" in c for c in calls) and "-r" in calls[-1]


@pytest.mark.parametrize("uname", [("Darwin", "x86_64"), ("Linux", "aarch64")])
def test_other_platforms_never_use_the_metal_index_1341(tmp_path, uname):
    rc, calls = _run(tmp_path, 0, uname=uname)
    assert rc == 0 and len(calls) == 1 and "-r" in calls[0] and "metal" not in calls[0]
