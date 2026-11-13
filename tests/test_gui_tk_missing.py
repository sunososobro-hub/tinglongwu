"""#328: without tkinter, `tlwu-gui` / `tlwu --gui` print one line with the install hint and exit non-zero."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import tinglongwu.gui as gui

ROOT = Path(__file__).resolve().parents[1]
BLOCK = "import sys; sys.modules['tkinter'] = None; "  # `import tkinter` then raises ImportError


def _check(r: subprocess.CompletedProcess) -> None:
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "Traceback" not in out, out
    assert r.stdout == ""
    lines = r.stderr.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("tkinter is missing."), out


def _py(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", BLOCK + code], capture_output=True, text=True, cwd=ROOT, timeout=60)


def test_tlwu_gui_entry_point_without_tkinter():
    _check(_py("import tinglongwu.gui as g; sys.exit(g.main())"))


def test_tlwu_dash_gui_without_tkinter():
    _check(_py("from tinglongwu.cli import main; sys.exit(main(['--gui']))"))


def _fake_system_python(monkeypatch, target):
    """Pretend /usr/bin/python3 exists and resolves to `target`; everything else resolves normally."""
    real = gui.os.path.realpath
    monkeypatch.setattr(gui.os.path, "exists", lambda p: True if p == "/usr/bin/python3" else gui.os.path.lexists(p))
    monkeypatch.setattr(gui.os.path, "realpath", lambda p: target if p == "/usr/bin/python3" else real(p))


def test_hint_names_apt_package_on_debian(monkeypatch):
    monkeypatch.setattr(gui.sys, "platform", "linux")
    me = gui.os.path.realpath(getattr(sys, "_base_executable", None) or sys.executable)
    monkeypatch.setattr(gui.shutil, "which", lambda name: {"apt-get": "/usr/bin/apt-get"}.get(name))
    _fake_system_python(monkeypatch, me)
    assert gui.tk_missing_hint() == "tkinter is missing. Install it with: sudo apt install python3-tk"


def test_hint_names_versioned_package_for_a_separate_python(monkeypatch):
    # e.g. deadsnakes python3.12 next to a system python3 of another version: python3-tk would not help.
    # #440: in an activated venv which("python3") is the venv's own python3 (same realpath as the base), so it must be ignored.
    monkeypatch.setattr(gui.sys, "platform", "linux")
    me = gui.os.path.realpath(getattr(sys, "_base_executable", None) or sys.executable)
    monkeypatch.setattr(gui.shutil, "which", lambda name: {"apt-get": "/usr/bin/apt-get", "python3": me}.get(name))
    _fake_system_python(monkeypatch, "/usr/bin/python3.10")
    v = sys.version_info
    assert gui.tk_missing_hint() == f"tkinter is missing. Install it with: sudo apt install python{v[0]}.{v[1]}-tk"


def test_hint_versioned_when_system_python3_missing(monkeypatch):
    monkeypatch.setattr(gui.sys, "platform", "linux")
    monkeypatch.setattr(gui.shutil, "which", lambda name: {"apt-get": "/usr/bin/apt-get"}.get(name))
    monkeypatch.setattr(gui.os.path, "exists", lambda p: False if p == "/usr/bin/python3" else gui.os.path.lexists(p))
    v = sys.version_info
    assert gui.tk_missing_hint().endswith(f"python{v[0]}.{v[1]}-tk")


def test_hint_is_generic_without_apt(monkeypatch):
    monkeypatch.setattr(gui.sys, "platform", "linux")
    monkeypatch.setattr(gui.shutil, "which", lambda name: None)
    h = gui.tk_missing_hint()
    assert "apt install" not in h and "Tk package" in h
