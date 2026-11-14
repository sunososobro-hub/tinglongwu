"""#113: real-Tk GUI tests must fail (not skip) when TLWU_REQUIRE_DISPLAY=1 and no display is available."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

# DISPLAY decides Tk only on X11; Windows / macOS Tk opens without it (home-PC pipeline runs pytest on Windows).
pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="X11-only check")

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


def _run_tmp(tmp_path, body, require):
    """Run a throwaway test file with this repo's conftest (the hooks under test) in a subprocess."""
    (tmp_path / "conftest.py").write_text((ROOT / "tests" / "conftest.py").read_text("utf-8"), "utf-8")
    (tmp_path / "test_tmp.py").write_text(body, "utf-8")
    env = {k: v for k, v in os.environ.items() if k != "TLWU_REQUIRE_DISPLAY"}
    if require:
        env["TLWU_REQUIRE_DISPLAY"] = "1"
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path)],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)


SKIPS = 'import pytest\n\ndef test_a():\n    pytest.skip("no display")\n\ndef test_b():\n    pytest.skip("other reason")\n'


@pytest.mark.parametrize("require,want", [(False, "2 skipped"), (True, "1 failed, 1 skipped")])
def test_require_flag_turns_only_display_skips_into_failures(tmp_path, require, want):
    r = _run_tmp(tmp_path, SKIPS, require)
    assert want in r.stdout, r.stdout + r.stderr


DIALOG = ('import pytest\ntk = pytest.importorskip("tkinter")\nfrom tkinter import messagebox\n\n'
          'def test_dialog():\n    assert messagebox.askyesno("Download?", "pack") is None\n\n'
          'def test_patched(monkeypatch):\n    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)\n'
          '    assert messagebox.askyesno("t", "m") is True\n')


def test_modal_dialog_fails_the_test_at_once_instead_of_blocking(tmp_path):
    pytest.importorskip("tkinter")
    r = _run_tmp(tmp_path, DIALOG, False)  # no display needed: the guard answers before Tk opens a window
    assert "unexpected modal dialog" in r.stdout and "messagebox.askyesno" in r.stdout, r.stdout + r.stderr
    assert "2 passed, 1 error" in r.stdout, r.stdout  # test_dialog errors at teardown, test_patched is fine
