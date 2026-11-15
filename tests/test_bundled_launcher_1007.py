"""#1007: in the core layout (packaging/launcher/tlwu_launcher.c) sys.executable is tlwu.exe, but the launcher's
TLWU_BUNDLED=1 does not reach os.environ. The in-app voice install then ran `tlwu.exe -m tinglongwu_setup --talk`,
which tinglongwu.cli rejects (install-clean speak-smoke, dev 75895e9: "unrecognized arguments: -m tinglongwu_setup")."""
import sys

import pytest

from tinglongwu.gui import autoupdate, core
from tlwu_app import main as app_main


@pytest.fixture
def core_layout(monkeypatch, tmp_path):
    exe = tmp_path / "app" / "tlwu.exe"
    monkeypatch.delenv("TLWU_BUNDLED", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "argv", [str(exe), "speak-smoke"])
    return exe


def test_voice_install_cmd_reruns_tlwu_exe_as_setup_child(core_layout, tmp_path):
    cmd = core.voice_install_cmd(tmp_path / "models")
    assert cmd[:2] == [str(core_layout), "--run-setup"]
    assert "-m" not in cmd and "--talk" in cmd


def test_is_bundled_without_the_env_var(core_layout):
    assert app_main.is_bundled() and autoupdate.is_bundled()


def test_prepare_env_marks_children_bundled(core_layout, monkeypatch, tmp_path):
    monkeypatch.setenv("TLWU_MODELS", str(tmp_path / "m"))
    app_main.prepare_env(app_main.is_bundled(), False)
    import os

    assert os.environ.get("TLWU_BUNDLED") == "1"  # inherited by the --run-setup child (downloader / models dir)


def test_source_python_is_not_bundled(monkeypatch, tmp_path):
    monkeypatch.delenv("TLWU_BUNDLED", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    assert not app_main.is_bundled() and not autoupdate.is_bundled()
    assert core.voice_install_cmd(tmp_path)[1:3] == ["-m", "tinglongwu_setup"]
