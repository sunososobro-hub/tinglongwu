from tinglongwu.translate import cjk_clean


def test_setup_gpu_flag(monkeypatch, tmp_path):
    from tinglongwu_setup import cli as scli

    pips = []
    monkeypatch.setattr(scli, "_pip", lambda *a: pips.append(a))
    assert scli.main(["--gpu", "--models-dir", str(tmp_path)]) == 0
    assert pips and pips[0][0] == "install" and pips[0][-1].endswith("requirements-gpu.txt")


def test_removed_m2m_engine_and_flag_are_rejected(tmp_path):
    import pytest

    from tinglongwu import cli
    from tinglongwu_setup import downloader

    with pytest.raises(SystemExit):
        cli.parse_args(["--engine", "m2m"])
    with pytest.raises(SystemExit):
        downloader.main(["--m2m", "--models-dir", str(tmp_path)])


def test_cjk_clean_for_direct_engines():
    assert cjk_clean("你好 ,事情 怎麼樣 ? 我 很 好 .") == "你好，事情怎麼樣？我很好。"
    assert cjk_clean("去 Boosty 玩") == "去 Boosty 玩"
