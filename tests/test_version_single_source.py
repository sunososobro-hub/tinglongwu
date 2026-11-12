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


def test_build_windows_follows_package_version():
    bw = _build_windows()
    assert bw.read_version() == tinglongwu.__version__
    assert bw.WIN_VERSION == tinglongwu.__version__ + ".0"
    cmd = bw.nuitka_cmd()
    assert f"--file-version={bw.WIN_VERSION}" in cmd
    assert f"--product-version={bw.WIN_VERSION}" in cmd


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
