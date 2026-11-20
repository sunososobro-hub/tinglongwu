"""#1677: install.sh ends on the GUI (desktop + TTY) or prints the commands; linux_menu has item G."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

if sys.platform == "win32":
    pytest.skip("tests Linux shell scripts (pty is Unix-only)", allow_module_level=True)
import pty  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "scripts" / "install.sh"
MENU = ROOT / "scripts" / "linux_menu.sh"


def _stub(tmp_path):
    gui = tmp_path / ".venv" / "bin" / "tlwu-gui"
    gui.parent.mkdir(parents=True)
    gui.write_text("#!/bin/sh\necho GUI-STARTED\n")
    gui.chmod(0o755)


def _env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    env.update(extra)
    return env


CODE = f'TLWU_SOURCE_ONLY=1 source "{INSTALL}"; DIR="$D"; finish_gui; echo MENU-REACHED'


def test_no_display_prints_commands_and_exits_zero(tmp_path):
    _stub(tmp_path)
    r = subprocess.run(["bash", "-c", CODE], env=_env(tmp_path, D=str(tmp_path)),
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0
    assert "GUI-STARTED" not in r.stdout
    out = r.stdout
    assert out.index("tlwu-gui") < out.index("linux_menu.sh")


def test_display_but_not_interactive_does_not_launch(tmp_path):
    _stub(tmp_path)
    r = subprocess.run(["bash", "-c", CODE], env=_env(tmp_path, D=str(tmp_path), DISPLAY=":0"),
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0 and "GUI-STARTED" not in r.stdout


def test_display_and_tty_launches_gui(tmp_path):
    _stub(tmp_path)
    env = _env(tmp_path, D=str(tmp_path), WAYLAND_DISPLAY="wayland-0")
    master, slave = pty.openpty()
    p = subprocess.Popen(["bash", "-c", CODE], env=env, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    os.close(slave)
    out = p.communicate(timeout=30)[0].decode()
    os.close(master)
    assert p.returncode == 0
    assert "GUI-STARTED" in out and "MENU-REACHED" not in out


def test_menu_has_gui_item():
    txt = MENU.read_text("utf-8")
    assert "g|G) exec tlwu-gui" in txt and "M_OPT_GUI" in txt
    assert subprocess.run(["bash", "-n", str(MENU)]).returncode == 0
    for lang in ("en", "ru", "zh-TW", "ja"):
        r = subprocess.run(["bash", "-c", f'. "{ROOT}/scripts/i18n/{lang}.sh"; printf %s "$M_OPT_GUI"'],
                           capture_output=True, text=True)
        assert "G)" in r.stdout


def test_printed_commands_are_shell_safe_with_spaces(tmp_path):
    d = tmp_path / "tlwu demo"
    _stub(d)
    r = subprocess.run(["bash", "-c", CODE], env=_env(d, D=str(d)),
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0
    assert "tlwu\\ demo/.venv/bin/tlwu-gui" in r.stdout
    assert "bash " + str(d).replace(" ", "\\ ") + "/scripts/linux_menu.sh" in r.stdout
