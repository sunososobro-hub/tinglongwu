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


def test_hint_names_apt_package_on_debian(monkeypatch):
    monkeypatch.setattr(gui.sys, "platform", "linux")
    monkeypatch.setattr(gui.shutil, "which", lambda name: "/usr/bin/apt-get" if name == "apt-get" else None)
    assert gui.tk_missing_hint() == "tkinter is missing. Install it with: sudo apt install python3-tk"


def test_hint_is_generic_without_apt(monkeypatch):
    monkeypatch.setattr(gui.sys, "platform", "linux")
    monkeypatch.setattr(gui.shutil, "which", lambda name: None)
    h = gui.tk_missing_hint()
    assert "apt install" not in h and "Tk package" in h
