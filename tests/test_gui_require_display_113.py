"""#113: real-Tk GUI tests must fail (not skip) when TLWU_REQUIRE_DISPLAY=1 and no display is available."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST = "tests/test_gui.py::test_start_shows_listening_in_main_window_and_bar_stays_hidden"


def _run(require):
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "TLWU_REQUIRE_DISPLAY")}
    if require:
        env["TLWU_REQUIRE_DISPLAY"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", TEST],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )


def test_without_display_default_skips():
    r = _run(False)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "skipped" in r.stdout


def test_without_display_and_require_flag_fails():
    r = _run(True)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "TLWU_REQUIRE_DISPLAY=1" in r.stdout
