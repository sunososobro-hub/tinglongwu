"""#1808: plain wording for the GPU / Vulkan offers and add-on prompts; the technical view keeps the old text."""
from __future__ import annotations

import re

import pytest

from tinglongwu import i18n
from tinglongwu.gui import core, gpu_offer

LANGS = ("en", "ru", "zh-TW", "ja")
KEYS = ("gpu_offer_banner", "vk_offer_banner", "vk_offer_running", "vk_offer_ok", "vk_offer_ok_cpu", "vk_offer_fail",
        "gpu_offer_ok", "gpu_offer_fail", "gpu_auto_running", "gpu_offer_running", "gpu_pack_row_name",
        "gpu_pack_row_unavailable", "gpu_vulkan_ready", "gpu_vulkan_nopack", "gpu_vulkan_unknown")
BANNED = re.compile(r"ASR|MT|GPU|Vulkan|CUDA|NVIDIA|pack|tier")


@pytest.fixture(autouse=True)
def _reset():
    core.set_tech_labels(setting=False, flag=False)
    yield
    core.set_tech_labels(setting=False, flag=False)


def test_plain_variants_have_no_technical_words_in_every_language():
    for k in KEYS:
        for lang in LANGS:
            assert not BANNED.search(core.LABELS[f"ui.{k}"][lang]["plain"]), (k, lang)


def test_tech_variant_is_the_current_i18n_text_and_placeholders_match():
    ph = lambda s: set(re.findall(r"\{(\w+)\}", s))  # noqa: E731
    for k in KEYS:
        for lang in LANGS:
            v = core.LABELS[f"ui.{k}"][lang]
            assert v["tech"] == i18n.MESSAGES[lang][k], (k, lang)
            assert ph(v["plain"]) <= ph(v["tech"]) | {"size", "name"}, (k, lang)
            assert ph(v["plain"]) >= ph(v["tech"]) - {"reason", "cpu"}, (k, lang)


def test_zh_tw_uses_the_owner_draft_wording():
    assert core.label("ui.gpu_offer_banner", "zh-TW", tech=False).startswith("你的電腦可以更快 — 要下載加速套件嗎")
    assert core.label("ui.vk_offer_fail", "zh-TW", tech=False) == "加速套件沒裝成功，先用一般速度，翻譯照常可用。"


def test_switch_changes_only_the_text_the_offer_shows():
    assert "Vulkan" not in gpu_offer.vulkan_text(["AMD Radeon"], True)
    assert core.ui_text("gpu_offer_banner", size="1.5 GB").count("1.5 GB") == 1
    core.set_tech_labels(setting=True)
    assert gpu_offer.vulkan_text(["AMD Radeon"], True) == i18n.t("gpu_vulkan_ready", name="AMD Radeon")
    assert core.ui_text("gpu_offer_fail", reason="x") == i18n.t("gpu_offer_fail", reason="x")
