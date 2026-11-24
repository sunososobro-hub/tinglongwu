"""#1809: plain / technical wording of the setup app (tlwu_app): behaviour of the text functions, no source reads."""
from __future__ import annotations

import re

import pytest

from tlwu_app import main as app_main
from tlwu_app import setup_gui, tiers, wording

JARGON = re.compile(r"ASR|MT|GPU|Vulkan|CUDA|pack|tier|beam|RTF|small|large", re.I)
GPU = tiers.Gpu("RTX Test", 8192, 7000, "560.1")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(wording, "tech", lambda: wording._TECH["flag"])  # no saved GUI setting in tests
    monkeypatch.setenv("TLWU_UI_LANG", "zh-TW")
    wording.set_tech(False)
    yield
    wording.set_tech(False)


def test_every_key_has_zh_tw_and_en_with_both_views():
    for key, per_lang in wording.TEXT.items():
        assert set(per_lang) == set(wording.LANGS), key
        for lang, pair in per_lang.items():
            assert len(pair) == 2 and all(x.strip() for x in pair), (key, lang)


def test_plain_text_has_no_jargon_in_either_language():
    for key, per_lang in wording.TEXT.items():
        for lang, (plain, _tech) in per_lang.items():
            assert not JARGON.search(plain), (key, lang, JARGON.search(plain).group())


def test_plain_is_the_default_and_tech_gives_the_old_text():
    assert wording.text("detect.force_cpu") == "已指定一般速度。"
    assert tiers.detection_text(None, force_cpu=True) == "已指定一般速度。"
    wording.set_tech(True)
    assert tiers.detection_text(None, force_cpu=True) == "已指定 CPU 模式。"
    assert tiers.T_CPU.label == tiers.T_CPU.label_zh


def test_language_is_zh_tw_unless_ui_lang_is_en(monkeypatch):
    assert wording.lang() == "zh-TW"
    monkeypatch.delenv("TLWU_UI_LANG")
    monkeypatch.setattr("tinglongwu.userdirs.read_config", lambda: {})
    assert wording.lang() == "zh-TW"
    monkeypatch.setenv("TLWU_UI_LANG", "en")
    assert wording.lang() == "en"
    assert tiers.detection_text(None, force_cpu=True) == "Normal speed selected."
    assert tiers.detection_text(GPU).startswith("Graphics chip found: RTX Test")


def test_tier_labels_plain_vs_tech():
    for t in (tiers.T_GPU, tiers.T_MIN, tiers.T_CPU):
        wording.set_tech(False)
        assert not JARGON.search(t.label)
        wording.set_tech(True)
        assert t.label == t.label_zh


def test_gpu_pack_offer_and_status_follow_the_switch():
    assert "GPU" not in setup_gui.gpu_pack_ask() and "加速套件" in setup_gui.gpu_pack_ask()
    assert "GPU" not in setup_gui.gpu_pack_status(1, "x") and "沒裝成功" in setup_gui.gpu_pack_status(1, "x")
    wording.set_tech(True)
    assert "GPU 加速包" in setup_gui.gpu_pack_ask()
    assert setup_gui.gpu_pack_status(0, "gpu pack 0.3.5: installed in x") == "GPU 加速包已安裝。"


def test_old_driver_hint_is_plain_then_technical():
    old = tiers.Gpu("RTX Old", 8192, 7000, "400.00")
    plain = tiers.driver_hint(old, "win32")
    assert "RTX Old" in plain and "400.00" in plain and not JARGON.search(plain.split("：")[0])
    wording.set_tech(True)
    assert "CUDA 12" in tiers.driver_hint(old, "win32")


def test_fallback_line_is_shown_plain_but_the_protocol_line_is_unchanged():
    line = tiers.gpu_fallback_line("init error")
    assert line == "GPU 無法使用，已改用 CPU：init error"
    assert wording.fallback_display(line) == "加速沒有成功，先用一般速度，翻譯照常可用：init error"
    wording.set_tech(True)
    assert wording.fallback_display(line) == line


def test_tech_labels_flag_turns_the_switch_on(monkeypatch):
    monkeypatch.setattr(app_main, "run_gui", lambda *a: 0)
    monkeypatch.setattr(app_main, "prepare_env", lambda *a: tiers.default_models_dir())
    assert wording._TECH["flag"] is False
    assert app_main.main(["--gui", "--tech-labels"]) == 0
    assert wording._TECH["flag"] is True
