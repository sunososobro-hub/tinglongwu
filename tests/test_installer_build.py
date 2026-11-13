"""#125 part 1: Inno Setup wizard build script (staging, app icon, ISCC command)."""
from __future__ import annotations

import importlib.util
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_installer", ROOT / "installer" / "build_installer.py")
bi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bi)


def _git_ok() -> bool:
    try:
        return subprocess.run(["git", "ls-files", "install.ps1"], cwd=ROOT, capture_output=True).stdout.strip() != b""
    except OSError:
        return False


needs_git = pytest.mark.skipif(not _git_ok(), reason="needs a git checkout")


@needs_git
def test_stage_ships_app_and_install_scripts_not_tests(tmp_path):
    files = bi.stage(tmp_path / "stage")
    must = ["install.ps1", "install.bat", "pyproject.toml", "requirements.txt", "requirements-llama-cuda.txt",
            "LICENSE", "README.md", "tinglongwu/__init__.py", "tinglongwu_setup/cli.py", "tlwu_app/main.py",
            "installer/run_install.ps1"]
    for m in must:
        assert m in files and (tmp_path / "stage" / m).is_file(), m
    for f in files:
        assert not f.startswith(("tests/", ".github/", "mobile/", "docs/", "tlwu_eval/", "models/", ".venv/")), f
    # the .iss is a build input, not shipped
    assert "installer/tinglongwu.iss" not in files


def _ico_entries(data: bytes) -> list[tuple[int, bytes]]:
    reserved, typ, n = struct.unpack_from("<HHH", data, 0)
    assert (reserved, typ) == (0, 1)
    out = []
    for i in range(n):
        w, h, _, _, planes, bpp, size, off = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        assert w == h and planes == 1 and bpp == 32
        out.append((w or 256, data[off:off + size]))
    return out


def _png_alpha(png: bytes) -> tuple[int, list[int]]:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h, depth, ctype = struct.unpack(">IIBB", png[16:26])
    assert (depth, ctype) == (8, 6) and w == h
    pos, idat = 8, b""
    while pos < len(png):
        ln, = struct.unpack(">I", png[pos:pos + 4])
        t, d = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + ln]
        assert zlib.crc32(t + d) & 0xFFFFFFFF == struct.unpack(">I", png[pos + 8 + ln:pos + 12 + ln])[0]
        if t == b"IDAT":
            idat += d
        pos += 12 + ln
    raw = zlib.decompress(idat)
    stride = 1 + 4 * w
    return w, [raw[y * stride + 1 + 4 * x + 3] for y in range(h) for x in range(w)]


def test_icon_is_multisize_ico_with_transparent_corners(tmp_path):
    ico = bi.make_icon(tmp_path / "a.ico", sizes=(16, 32, 48))
    entries = _ico_entries(ico.read_bytes())
    assert [s for s, _ in entries] == [16, 32, 48]
    for s, png in entries:
        w, alpha = _png_alpha(png)
        assert w == s
        assert alpha[0] == 0                       # rounded corner is transparent
        assert alpha[(s // 2) * s + s // 2] == 255  # centre is opaque


def test_icon_256_uses_zero_size_byte(tmp_path):
    data = bi.make_icon(tmp_path / "b.ico", sizes=(256,)).read_bytes()
    assert data[6] == 0 and _ico_entries(data)[0][0] == 256


def test_iscc_command_passes_every_define_the_iss_uses(tmp_path):
    cmd = bi.iscc_cmd("ISCC.exe", tmp_path / "stage", tmp_path / "i.ico", tmp_path, "1.2.3")
    assert bi.missing_defines(bi.ISS.read_text("utf-8-sig"), cmd) == []
    assert "/DAppVersion=1.2.3" in cmd and cmd[-1] == str(bi.ISS)
    # and the check really catches a define that is not passed
    assert bi.missing_defines('#define Own "a"\nx={#StageDir} y={#Nope} z={#Own}', cmd) == ["Nope"]


@needs_git
def test_cli_dry_run_and_missing_iscc(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bi, "ICON_SIZES", (16,))
    monkeypatch.setattr(bi, "find_iscc", lambda: None)
    assert bi.main(["--dry-run", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert f"/DStageDir={tmp_path / 'stage'}" in out and (tmp_path / "tinglongwu.ico").is_file()
    assert (tmp_path / "stage" / "install.ps1").is_file()
    assert bi.main(["--out", str(tmp_path)]) == 2
    assert "ISCC.exe not found" in capsys.readouterr().err


def test_app_version_is_package_version():
    import tinglongwu

    assert bi.app_version() == tinglongwu.__version__ != "0.0.0"


def test_script_runs_standalone(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "installer" / "build_installer.py"), "--help"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "--dry-run" in r.stdout
