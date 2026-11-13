"""#227 / #331: menu speak mode is always Hy-MT2; when it is not installed yet the menu also prints the install hint."""
import subprocess
import sys

import pytest

from tinglongwu import menuopts


@pytest.fixture(autouse=True)
def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("TLWU_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("TLWU_UI_LANG", "en")


@pytest.mark.parametrize("to", ["ru", "en", "ja", "follow"])
def test_installed_gives_hymt_without_asking(to, monkeypatch):
    asked = []
    assert menuopts.speak_mt(to, ask=lambda p: asked.append(p) or "o", say=lambda m: None,
                             hymt_ready=lambda d: True) == "hymt" and asked == []
    monkeypatch.setenv("TLWU_MT", "opus")  # #331: no OPUS on the speak side any more, the old override is ignored
    assert menuopts.speak_mt(to, say=lambda m: None, hymt_ready=lambda d: True) == "hymt"


def test_listen_cpu_defaults_to_hymt_without_asking(monkeypatch):
    asked = []
    kw = dict(ask=lambda p: asked.append(p) or "", say=lambda m: None, device=lambda: "cpu", mdir=".",
              cpu_choice=lambda: None, hymt_ready=lambda d: True)
    assert menuopts.listen_args(**kw)[-2:] == ["--engine", "hymt"] and asked == []
    monkeypatch.setenv("TLWU_MT", "opus")  # #471: OPUS is gone from listen too, the old override is ignored
    assert menuopts.listen_args(**kw)[-2:] == ["--engine", "hymt"]


@pytest.mark.parametrize("to", ["ru", "en", "ja", "follow"])
def test_not_installed_gives_hymt_and_hint_without_asking(to):
    said, asked = [], []
    assert menuopts.speak_mt(to, ask=lambda p: asked.append(p) or "h", say=said.append,
                             hymt_ready=lambda d: False) == "hymt"
    assert asked == [] and len(said) == 1
    assert "pip install llama-cpp-python --only-binary=:all:" in said[0] and "tlwu-setup --hymt" in said[0]


def test_cli_prints_engine_and_survives_eof(tmp_path):
    import os

    env = {**os.environ, "TLWU_MODELS": str(tmp_path / "empty")}   # no Hy-MT2 GGUF: the hint must be printed
    r = subprocess.run([sys.executable, "-m", "tinglongwu.menuopts", "speak", "ru"], input="",
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout.strip() == "hymt"
    assert "tlwu-setup --hymt" in r.stderr
