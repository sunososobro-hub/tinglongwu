"""#671: the first setup step asks which language to understand and installs only that pack."""
import pytest

from tinglongwu_setup import cli, downloader, packs


@pytest.mark.parametrize("loc,want", [("ja_JP.UTF-8", "ja"), ("ko_KR.UTF-8", "ko"), ("zh_TW.UTF-8", "zh"),
                                      ("en_US.UTF-8", "en"), ("de_DE", "en"), ("", "en")])
def test_locale_default(loc, want):
    assert packs.locale_default({"LANG": loc}) == want


def test_prompt_shows_tier1_and_size_line_in_three_ui_languages():
    for ui, needle in (("en", "Needs internet to download (1100 MB). Works offline afterwards."),
                       ("ru", "1100 МБ"), ("zh", "1100 MB")):
        seen = []
        packs.choose_langs({"LANG": "ja_JP", "TLWU_UI_LANG": ui}, ask=lambda q: seen.append(q) or "")
        assert needle in seen[0]
        assert all(l in seen[0] for l in ("zh", "en", "ja", "ko", "ru"))


def test_explicit_packs_and_yes_skip_the_prompt():
    def boom(_):
        raise AssertionError("must not prompt")
    assert packs.choose_langs({"TLWU_PACKS": "ja,ru", "LANG": "en_US"}, ask=boom) == "ja,ru"
    assert packs.choose_langs({"TLWU_YES": "1", "LANG": "ko_KR"}, ask=boom) == "ko"


def test_empty_answer_takes_locale_default_and_multi_select():
    assert packs.choose_langs({"LANG": "ja_JP"}, ask=lambda q: "") == "ja"
    assert packs.choose_langs({"LANG": "en_US"}, ask=lambda q: "ko, ru xx ko") == "ko,ru"


def test_ask_lang_installs_exactly_chosen_packs(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("TLWU_PACKS", raising=False)
    monkeypatch.delenv("TLWU_YES", raising=False)
    monkeypatch.delenv("TLWU_OFFLINE", raising=False)
    monkeypatch.setattr("builtins.input", lambda q: "ja ru")
    got = []
    monkeypatch.setattr(downloader, "main", lambda argv: got.append(argv) or 0)
    assert cli.main(["--ask-lang", "--models-dir", str(tmp_path)]) == 0
    assert got[0][got[0].index("--packs") + 1] == "ja,ru"
    assert packs.keys_for("ja,ru") == ["hymt2-1.8b", packs.PIPER_RU_DEFAULT]


def test_ask_lang_prints_size_of_the_selected_packs(monkeypatch, tmp_path, capsys):
    for k in ("TLWU_PACKS", "TLWU_YES", "TLWU_OFFLINE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setattr("builtins.input", lambda q: "ru")
    monkeypatch.setattr(downloader, "main", lambda argv: 0)
    assert cli.main(["--ask-lang", "--models-dir", str(tmp_path)]) == 0
    assert "language packs: ru (download: 1160 MB)" in capsys.readouterr().out


def test_korean_source_needs_its_pack_on_demand():
    from tinglongwu.gui import pack_on_demand as pod
    assert pod.PACK_OF["ko"] == "ko" and pod.PACKS["ko"] == packs.PACKS["ko"]
