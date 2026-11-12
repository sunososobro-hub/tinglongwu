"""#227: Linux menu speak mode picks Hy-MT2 for zh->ru when installed, OPUS plus an install hint otherwise."""
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
    monkeypatch.setenv("TLWU_MT", "opus")
    assert menuopts.speak_mt(to, say=lambda m: None, hymt_ready=lambda d: True) == "opus"


def test_listen_cpu_defaults_to_hymt_without_asking(monkeypatch):
    asked = []
    kw = dict(ask=lambda p: asked.append(p) or "", say=lambda m: None, device=lambda: "cpu", mdir=".",
              cpu_choice=lambda: None, hymt_ready=lambda d: True)
    assert menuopts.listen_args(**kw)[-2:] == ["--engine", "hymt"] and asked == []
    monkeypatch.setenv("TLWU_MT", "opus")
    assert "--engine" not in menuopts.listen_args(**kw)


@pytest.mark.parametrize("to", ["ru", "en", "ja", "follow"])
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
