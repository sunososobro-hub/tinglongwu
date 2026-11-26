"""#1808 part 2: plain wording for the add-on prompts that name a language pack, and the pack / voice / speaker
download questions; the technical view keeps the old text."""
from __future__ import annotations

import re

import pytest

from tinglongwu import i18n
from tinglongwu.gui import core, pack_on_demand, speaker_pack, voice_install

LANGS = ("en", "ru", "zh-TW", "ja")
KEYS = ("addons_downloading", "addons_pack_ok", "addons_pack_fail", "addons_remove_confirm", "addons_removed",
        "addons_still", "addons_import_hint")
PACKS = ("zh", "en", "ja", "ko", "ru", "hymt2")
BANNED = re.compile(r"ASR|MT|GPU|Vulkan|CUDA|NVIDIA|pack|tier|Kokoro")
PH = re.compile(r"\{(\w+)\}")


@pytest.fixture(autouse=True)
def _reset():
    core.set_tech_labels(setting=False, flag=False)
    yield
    core.set_tech_labels(setting=False, flag=False)


def test_plain_prompts_have_no_technical_words_and_name_the_pack():
    for k in KEYS:
        for lang in LANGS:
            v = core.LABELS[f"ui.{k}"][lang]
            assert not BANNED.search(v["plain"]), (k, lang)
            assert v["tech"] == i18n.MESSAGES[lang][k], (k, lang)  # tech view = the unchanged i18n text
            assert set(PH.findall(v["plain"])) == ({"name"} if "{pack}" in v["tech"] else set()), (k, lang)


def test_every_pack_has_a_plain_name_and_the_id_as_tech_name():
    for p in PACKS:
        for lang in LANGS:
            assert core.LABELS[f"pack.{p}"][lang]["tech"] == p
            assert not BANNED.search(core.pack_name(p, lang)) and core.pack_name(p, lang) != p, (p, lang)
    assert core.pack_name("ja", "zh-TW") == "日文"
    assert core.pack_name("Topic notes") == "Topic notes"  # names with no entry pass through


def test_switch_flips_the_prompt_between_plain_name_and_pack_id():
    assert core.ui_text("addons_pack_ok", pack="ru", name=core.pack_name("ru", "en")) == "Russian add-on installed."
    core.set_tech_labels(setting=True)
    assert core.ui_text("addons_pack_ok", pack="ru", name=core.pack_name("ru")) == i18n.t("addons_pack_ok", pack="ru")


@pytest.mark.parametrize("lang", LANGS)
def test_download_questions_are_plain_by_default_and_old_text_when_technical(lang):
    plain = [pack_on_demand.dialog_text(lang, "ja", 1200), speaker_pack.dialog_text(lang, 30),
             voice_install.dialog_text(lang)]
    for s in plain:
        assert not BANNED.search(s), s
    assert core.pack_name("ja", lang) in plain[0]
    core.set_tech_labels(setting=True)
    tag = lang if lang in pack_on_demand.TEXT else "en"
    assert pack_on_demand.dialog_text(lang, "ja", 1200) == pack_on_demand.TEXT[tag].format(pack="ja", mb=1200, min=2)
    assert speaker_pack.dialog_text(lang, 30) == speaker_pack.TEXT.get(lang, speaker_pack.TEXT["en"]).format(mb=30)
    assert "Kokoro" in voice_install.dialog_text(lang)
