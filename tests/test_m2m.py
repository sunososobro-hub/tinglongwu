import pytest

from tinglongwu import cli
from tinglongwu.models import ModelNotFound
from tinglongwu.translate import M2M_CODES, M2mTranslator, find_m2m


def test_m2m_missing_model_names_setup_command(tmp_path):
    with pytest.raises(ModelNotFound, match="--m2m"):
        M2mTranslator(tmp_path)


def test_m2m_codes_and_dir(tmp_path):
    (tmp_path / "m2m-1.2B").mkdir()
    assert find_m2m(tmp_path).name == "m2m-1.2B"
    t = M2mTranslator(tmp_path)
    assert t._code("ru") == "__ru__" and M2M_CODES["zh"] == "__zh__"
    with pytest.raises(RuntimeError, match="M2mTranslator.*code table"):
        t.preload("xx", "zh")


def test_cli_engines():
    assert cli.parse_args(["--engine", "m2m"]).engine == "m2m"
    for bad in (["--engine", "nllb"], ["--m2m-size", "418M"]):
        with pytest.raises(SystemExit):
            cli.parse_args(bad)


def test_setup_m2m_option_and_gpu_flag(monkeypatch, tmp_path):
    from tinglongwu_setup import cli as scli
    from tinglongwu_setup import downloader

    for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        monkeypatch.setenv(k, "1")  # downloader.main edits these; monkeypatch restores them
    calls = []
    monkeypatch.setattr(downloader, "get_m2m", lambda man, mdir: calls.append(1))
    assert downloader.main(["--m2m", "--models-dir", str(tmp_path)]) == 0
    assert calls == [1]
    pips = []
    monkeypatch.setattr(scli, "_pip", lambda *a: pips.append(a))
    assert scli.main(["--gpu", "--models-dir", str(tmp_path)]) == 0
    assert pips and pips[0][0] == "install" and pips[0][-1].endswith("requirements-gpu.txt")


def test_get_m2m_without_converter_exits_with_hint(monkeypatch, tmp_path):
    import subprocess

    from tinglongwu_setup import downloader

    class R:
        returncode = 1

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    with pytest.raises(SystemExit) as e:
        downloader.get_m2m({}, tmp_path)
    assert "TLWU_CONVERTER_PYTHON" in str(e.value)


def test_cjk_clean_for_direct_engines():
    from tinglongwu.translate import cjk_clean

    assert cjk_clean("你好 ,事情 怎麼樣 ? 我 很 好 .") == "你好，事情怎麼樣？我很好。"
    assert cjk_clean("去 Boosty 玩") == "去 Boosty 玩"
