"""#1057: a Japanese UI table, so ui_lang=ja (written by the #1002 setup wizard) or a Japanese system opens a
Japanese app instead of an English one."""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

from tinglongwu import i18n, langs
from tinglongwu.gui import core


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("TLWU_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(i18n, "_override", None)
    monkeypatch.setattr(i18n, "_cached", None)


@pytest.mark.parametrize("tag", ["ja", "ja-JP", "ja_JP.UTF-8", "JA_jp"])
def test_japanese_locale_tags_normalize_to_ja(tag):
    assert i18n.normalize(tag) == "ja"


@pytest.mark.parametrize("tag", ["jv", "jav", "en-US", ""])
def test_other_tags_are_not_japanese(tag):
    assert i18n.normalize(tag) != "ja"


def test_ja_table_has_every_english_key_with_same_placeholders():
    import tinglongwu.fmt  # noqa: F401  adds label_me / label_them to every table

    en, ja = i18n.MESSAGES["en"], i18n.MESSAGES["ja"]
    assert len(en) >= 202 and set(ja) == set(en)
    ph = re.compile(r"\{[^{}]*\}")
    for k, v in en.items():
        assert sorted(ph.findall(ja[k])) == sorted(ph.findall(v)), k
        ja[k].format(**{m.split("!")[0]: "x" for m in re.findall(r"\{([^{}:]*)", ja[k])})  # no broken braces


def ph_strip(s: str) -> str:
    return re.sub(r"\{[^{}]*\}", "", s)


def test_ja_text_is_japanese_not_english_copies():
    en, ja = i18n.MESSAGES["en"], i18n.MESSAGES["ja"]
    kana = re.compile(r"[぀-ヿ一-鿿]")
    letters = [k for k in en if re.search(r"[A-Za-z]", ph_strip(en[k]))]  # skip pure punctuation like ", "
    assert [k for k in letters if not kana.search(ja[k])] == []
    assert [k for k in en if ja[k] == en[k]] == []


def test_env_ja_gives_japanese_labels(monkeypatch):
    monkeypatch.setenv("TLWU_UI_LANG", "ja")
    assert i18n.ui_lang() == "ja"
    assert i18n.t("gui_ui_lang") == "表示言語"
    assert i18n.t("gui_start") == "スタート"
    assert i18n.t("gui_loading", s=5) == "⏳ 5 秒"


def test_wizard_config_ui_lang_ja_opens_japanese(monkeypatch):
    """#1002 writes ui_lang=ja into the config file; the next launch reads it."""
    monkeypatch.delenv("TLWU_UI_LANG", raising=False)
    from tinglongwu import userdirs

    userdirs.write_config_value("ui_lang", "ja")
    assert i18n.ui_lang() == "ja"
    assert i18n.t("gui_stop") == "停止"


def test_cli_prints_japanese(tmp_path):
    env = {**os.environ, "TLWU_UI_LANG": "ja", "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", "from tinglongwu import i18n; print(i18n.ui_lang(), i18n.t('gui_ui_lang'))"],
                         capture_output=True, text=True, encoding="utf-8", env=env, timeout=60).stdout
    assert out.strip() == "ja 表示言語"


def test_gui_dropdown_lists_japanese():
    from tinglongwu.gui.app import UI_LANGS

    assert UI_LANGS["日本語"] == "ja"
    assert sorted(UI_LANGS.values()) == sorted(["en", "ru", "zh-TW", "ja"])


def test_dropdowns_and_tour_are_japanese_and_round_trip():
    assert langs.label("auto", "ja") == "自動"
    assert langs.label("ko", "ja") == "韓国語 (試験的)"
    assert langs.from_label("韓国語 (試験的)") == "ko"
    assert langs.name("zh-tw", "ja") == "繁体字中国語"
    assert "ja 日本語、en 英語、ru ロシア語" in langs.menu_help("ja")
    assert core.tour_text("ja")["ok"] == "わかった"
    for kind, value in (("quality", "quality"), ("source", "mic"), ("preset", "chat"), ("engine", "auto")):
        lab = core.choice_label(kind, value, "ja")
        assert lab != core.choice_label(kind, value, "en")


def test_chat_labels_in_japanese(monkeypatch):
    monkeypatch.setenv("TLWU_UI_LANG", "ja")
    import tinglongwu.fmt  # noqa: F401

    assert i18n.t("label_me") == "▶ 自分" and i18n.t("label_them") == "◀ 相手"
