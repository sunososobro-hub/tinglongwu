"""#967: tlwu-setup and the app resolve the same models dir on the packaged layout."""
from pathlib import Path

import pytest

from tinglongwu import models
from tinglongwu_setup import downloader


@pytest.mark.parametrize("bundled", ["1", ""])
def test_setup_and_app_same_dir(monkeypatch, tmp_path, bundled):
    monkeypatch.delenv("TLWU_MODELS", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("TLWU_BUNDLED", bundled)
    assert downloader.default_models_dir() == models.models_dir(None)
    if bundled:
        assert models.models_dir(None) == tmp_path / "tinglongwu" / "models"


def test_env_wins_in_both(monkeypatch, tmp_path):
    monkeypatch.setenv("TLWU_MODELS", str(tmp_path / "m"))
    assert downloader.default_models_dir() == models.models_dir(None) == Path(tmp_path / "m")


def test_frozen_layout_uses_localappdata(monkeypatch, tmp_path):
    import sys

    monkeypatch.delenv("TLWU_MODELS", raising=False)
    monkeypatch.delenv("TLWU_BUNDLED", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert downloader.default_models_dir() == models.models_dir(None) == tmp_path / "tinglongwu" / "models"
