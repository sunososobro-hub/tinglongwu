import urllib.request

import pytest

from tinglongwu import cli


def test_missing_models_clear_error_no_download(wav, tmp_path, capsys, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("download attempted")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(urllib.request, "urlretrieve", boom)
    rc = cli.main(["--file", str(wav), "--models-dir", str(tmp_path / "nomodels")])
    err = capsys.readouterr().err
    assert rc == 2
    assert "not found" in err and "tlwu-setup" in err


def test_engine_default_and_to():
    assert cli.parse_args(["--to", "ko"]).to == "ko"
    assert cli.parse_args([]).engine == "auto" and cli.parse_args([]).to == "zh"


def test_zh_variant_arg():
    assert cli.parse_args([]).zh_variant == "tw"
    assert cli.parse_args(["--zh-variant", "cn"]).zh_variant == "cn"
    with pytest.raises(SystemExit):
        cli.parse_args(["--zh-variant", "xx"])
