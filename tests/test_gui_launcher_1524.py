"""#1524: the shortcuts start tlwu-gui.exe (WINDOWS subsystem, no console / Windows Terminal tab); tlwu.exe stays CONSOLE."""
import shutil
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))
import build_windows as bw  # noqa: E402
import runtime_layer as rt  # noqa: E402


def _pe(subsystem: int, machine: int = 0x8664) -> bytes:
    """Smallest PE32+ header block that carries IMAGE_OPTIONAL_HEADER.Subsystem."""
    pe = 0x80
    data = bytearray(pe + 4 + 20 + 112)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, pe)
    data[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<H", data, pe + 4, machine)
    struct.pack_into("<H", data, pe + 4 + 20 + 68, subsystem)
    return bytes(data)


def test_pe_subsystem_reads_gui_and_console():
    assert bw.pe_subsystem(_pe(2)) == bw.PE_SUBSYSTEM_WINDOWS_GUI == 2
    assert bw.pe_subsystem(_pe(3)) == bw.PE_SUBSYSTEM_WINDOWS_CUI == 3
    assert bw.pe_subsystem(b"not a pe file") is None


def test_gui_build_uses_windows_subsystem_and_console_build_does_not(tmp_path):
    rc = tmp_path / "tlwu.rc"
    gui = rt._launcher_builds(tmp_path / "g.exe", tmp_path, rc, tmp_path / "s.c", gui=True)
    cui = rt._launcher_builds(tmp_path / "c.exe", tmp_path, rc, tmp_path / "s.c")
    if not gui:
        pytest.skip("no C toolchain on this machine")
    flat = lambda builds: [" ".join(c) for _, cmds in builds for c in cmds]  # noqa: E731
    assert all(("-mwindows" in c) or ("SUBSYSTEM:WINDOWS" in c) or "windres" in c or "rc " in c or " rc" in c
               for c in flat(gui) if "-O2" in c or "/link" in c)
    assert not any("-mwindows" in c or "SUBSYSTEM:WINDOWS" in c for c in flat(cui))


def test_built_launchers_have_expected_subsystems(tmp_path):
    gcc = shutil.which("x86_64-w64-mingw32-gcc")
    if not (gcc and shutil.which("x86_64-w64-mingw32-windres")):
        pytest.skip("MinGW-w64 cross compiler not installed")
    exe, gui = tmp_path / "tlwu.exe", tmp_path / "tlwu-gui.exe"
    rt.build_launcher(exe, tmp_path / "w1")
    rt.build_launcher(gui, tmp_path / "w2", gui=True)
    assert bw.pe_subsystem(exe.read_bytes()) == 3
    assert bw.pe_subsystem(gui.read_bytes()) == 2
