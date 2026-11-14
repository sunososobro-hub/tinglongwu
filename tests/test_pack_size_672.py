"""#672: size of the language packs the Windows wizard shows and passes to `tlwu-setup --packs`."""
import pytest

from tinglongwu_setup import cli, packs


def test_total_size_counts_shared_model_once():
    assert packs.total_size_mb("zh,en") == 0
    assert packs.total_size_mb("ja") == 1100
    assert packs.total_size_mb("ru") == 1160
    assert packs.total_size_mb("ja,ru") == 1160  # Hy-MT2 is shared


def test_pack_size_cli_prints_mb(capsys):
    assert cli.main(["--pack-size", "ja"]) == 0
    assert capsys.readouterr().out.strip() == "1100"


def test_pack_size_rejects_unknown_pack():
    with pytest.raises(SystemExit):
        cli.main(["--pack-size", "xx"])
