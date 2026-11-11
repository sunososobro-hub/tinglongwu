"""#227: Linux menu speak mode picks Hy-MT2 for zh->ru when installed, OPUS plus an install hint otherwise."""
import subprocess
import sys

import pytest

from tinglongwu import menuopts


@pytest.fixture(autouse=True)
def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("TLWU_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("TLWU_UI_LANG", "en")


def test_ru_defaults_to_hymt_when_installed():
    assert menuopts.speak_mt("ru", ask=lambda p: "", say=lambda m: None, hymt_ready=lambda d: True) == "hymt"
    assert menuopts.speak_mt("ru", ask=lambda p: "o", say=lambda m: None, hymt_ready=lambda d: True) == "opus"


def test_en_defaults_to_opus_h_picks_hymt():
    assert menuopts.speak_mt("en", ask=lambda p: "", say=lambda m: None, hymt_ready=lambda d: True) == "opus"
    assert menuopts.speak_mt("en", ask=lambda p: "h", say=lambda m: None, hymt_ready=lambda d: True) == "hymt"


@pytest.mark.parametrize("to", ["ru", "en"])
def test_not_installed_gives_opus_and_hint_without_asking(to):
    said, asked = [], []
    assert menuopts.speak_mt(to, ask=lambda p: asked.append(p) or "h", say=said.append,
                             hymt_ready=lambda d: False) == "opus"
    assert asked == [] and len(said) == 1
    assert "pip install llama-cpp-python --only-binary=:all:" in said[0] and "tlwu-setup --hymt" in said[0]


def test_cli_prints_engine_and_survives_eof():
    r = subprocess.run([sys.executable, "-m", "tinglongwu.menuopts", "speak", "ru"], input="",
                       capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() in ("opus", "hymt")
    assert "tlwu-setup --hymt" in r.stderr or r.stdout.strip() == "hymt"
