from tinglongwu import models
from tinglongwu.gui import core


def _mk(tmp_path, *sizes):
    for s in sizes:
        (tmp_path / f"whisper-{s}").mkdir()


def test_uses_wanted_when_downloaded(tmp_path):
    _mk(tmp_path, "small", "large-v3-turbo")
    assert models.pick_whisper("small", tmp_path) == "small"


def test_falls_back_to_downloaded_model(tmp_path):
    _mk(tmp_path, "large-v3-turbo")
    assert models.pick_whisper("small", tmp_path) == "large-v3-turbo"
    assert models.whisper_installed(tmp_path) == ["large-v3-turbo"]


def test_nothing_downloaded_keeps_wanted_so_setup_hint_shows(tmp_path):
    assert models.pick_whisper("small", tmp_path) == "small"


def test_gui_argv_uses_downloaded_model(tmp_path, monkeypatch):
    _mk(tmp_path, "large-v3-turbo")
    monkeypatch.setenv("TLWU_MODELS", str(tmp_path))
    argv = core.settings_to_argv({"model": "small"})
    assert argv[argv.index("--model") + 1] == "large-v3-turbo"
