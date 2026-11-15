"""One source of truth for the version: tinglongwu.__version__ (#462)."""
import importlib.util
import tomllib
from pathlib import Path

import pytest

import tinglongwu
from tinglongwu import cli

ROOT = Path(__file__).resolve().parent.parent


def _build_windows():
    spec = importlib.util.spec_from_file_location("build_windows_462", ROOT / "packaging" / "build_windows.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pyproject_has_no_literal_version():
    proj = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    assert "version" not in proj["project"]
    assert "version" in proj["project"]["dynamic"]
    assert proj["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "tinglongwu.__version__"}


def test_build_windows_follows_package_version(tmp_path):
    bw = _build_windows()
    assert bw.read_version() == tinglongwu.__version__
    assert bw.WIN_VERSION == tinglongwu.__version__ + ".0"
    # #923: the launcher is compiled with version 0.0.0.0; the app layer stamps WIN_VERSION into the exe
    exe = tmp_path / "tlwu.exe"
    fixed = bw.VS_FIXEDFILEINFO_SIG + bytes(bw.VERSION_OFFSET - len(bw.VS_FIXEDFILEINFO_SIG)) + bytes(16)
    exe.write_bytes(b"MZ" + bytes(64) + fixed + b"tail")
    assert bw.set_exe_version(exe, bw.WIN_VERSION)
    assert ".".join(map(str, bw.pe_file_version(exe.read_bytes()))) == bw.WIN_VERSION
    assert exe.read_bytes().endswith(b"tail") and exe.stat().st_size == 2 + 64 + len(fixed) + 4
    exe.write_bytes(b"MZ no version resource")
    assert not bw.set_exe_version(exe, bw.WIN_VERSION) and exe.read_bytes() == b"MZ no version resource"


def test_win_version_pads_to_four_parts():
    bw = _build_windows()
    assert bw.win_version("1.2") == "1.2.0.0"
    assert bw.win_version("0.3.1+local") == "0.3.1.0"
    assert bw.win_version("1.2.3rc1") == "1.2.0.0"


def test_cli_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        cli.parse_args(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.startswith(f"tinglongwu {tinglongwu.__version__}")


def test_exe_entry_version_flag(capsys):
    from tlwu_app.main import main

    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.startswith(f"tinglongwu {tinglongwu.__version__}")


def test_version_is_plain_semver():
    import re

    assert re.fullmatch(r"\d+\.\d+\.\d+", tinglongwu.__version__)
