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


def _clean_env(monkeypatch):
    for k in ("TLWU_PACKS", "TLWU_YES", "TLWU_OFFLINE"):
        monkeypatch.delenv(k, raising=False)


def test_saved_choice_is_reused_without_prompt_1673(monkeypatch, tmp_path, capsys):
    _clean_env(monkeypatch)
    answers = iter(["ja ru"])
    monkeypatch.setattr("builtins.input", lambda q: next(answers))
    got = []
    monkeypatch.setattr(downloader, "main", lambda argv: got.append(argv) or 0)
    assert cli.main(["--ask-lang", "--models-dir", str(tmp_path)]) == 0  # first install asks
    assert got[0][got[0].index("--packs") + 1] == "ja,ru"

    def boom(_):
        raise EOFError  # stdin closed: must not even be asked
    monkeypatch.setattr("builtins.input", boom)
    capsys.readouterr()
    assert cli.main(["--ask-lang", "--models-dir", str(tmp_path)]) == 0
    assert got[1][got[1].index("--packs") + 1] == "ja,ru"
    assert "Languages: ja,ru (from previous setup)" in capsys.readouterr().out


def test_no_saved_choice_still_prompts_1673(monkeypatch, tmp_path):
    _clean_env(monkeypatch)
    asked = []
    monkeypatch.setattr("builtins.input", lambda q: asked.append(q) or "ko")
    monkeypatch.setattr(downloader, "main", lambda argv: 0)
    assert cli.main(["--ask-lang", "--models-dir", str(tmp_path)]) == 0
    assert len(asked) == 1 and packs.saved_langs(tmp_path) == "ko"


def test_env_override_beats_saved_choice_1673(monkeypatch, tmp_path):
    _clean_env(monkeypatch)
    packs.save_langs(tmp_path, "ja")
    monkeypatch.setenv("TLWU_PACKS", "ru")
    got = []
    monkeypatch.setattr(downloader, "main", lambda argv: got.append(argv) or 0)
    assert cli.main(["--ask-lang", "--models-dir", str(tmp_path)]) == 0
    assert got[0][got[0].index("--packs") + 1] == "ru"


def test_corrupt_saved_choice_asks_again_1673(tmp_path):
    (tmp_path / packs.SAVED_FILE).write_text("{not json", "utf-8")
    assert packs.saved_langs(tmp_path) == ""
