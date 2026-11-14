"""#607: helper scripts must spawn Windows children with CREATE_NO_WINDOW (no flashing consoles on the CI PC)."""
import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console flag")
def test_ci_e2e_nowin_flag():
    assert _load("ci_e2e").NOWIN == {"creationflags": subprocess.CREATE_NO_WINDOW}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console flag")
def test_ci_e2e_doctor_child_has_no_window():
    m = _load("ci_e2e")
    seen = {}

    def fake_run(*a, **kw):
        seen.update(kw)
        return mock.Mock(stdout="", stderr="", returncode=0)

    args = mock.Mock(to="ru", models_dir=None)
    with mock.patch.object(m.subprocess, "run", fake_run):
        try:
            m.cmd_doctor(args)
        except BaseException:  # noqa: BLE001 - only the spawn kwargs matter
            pass
    assert seen.get("creationflags") == subprocess.CREATE_NO_WINDOW


def test_ci_e2e_nowin_empty_off_windows():
    if sys.platform != "win32":
        assert _load("ci_e2e").NOWIN == {}
