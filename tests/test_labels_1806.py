"""#1806: plain / tech label structure, the "Show technical details" switch and the --tech-labels flag."""
from __future__ import annotations

import pytest

from tinglongwu import i18n
from tinglongwu.gui import core

LANGS = ("en", "ru", "zh-TW", "ja")


@pytest.fixture(autouse=True)
def _reset():
    core.set_tech_labels(setting=False, flag=False)
    yield
    core.set_tech_labels(setting=False, flag=False)


def test_label_variants_cover_every_key_language_and_variant():
    for key, per_lang in core.LABELS.items():
        assert set(per_lang) == set(LANGS), key
        for lang in LANGS:
            assert set(per_lang[lang]) == set(core.LABEL_VARIANTS), (key, lang)
            assert all(per_lang[lang][v].strip() for v in core.LABEL_VARIANTS), (key, lang)


def test_quality_tiers_are_in_the_new_structure():
    assert {f"quality.{q}" for q in core.QUALITIES} <= set(core.LABELS)


def test_switch_off_gives_plain_and_on_gives_the_old_technical_text():
    assert core.DEFAULTS["tech_labels"] is False
    assert core.label("quality.quality", "en") == "Most accurate, slower"
    assert core.label("quality.fast", "zh-TW") == "快一點"
    core.set_tech_labels(setting=True)
    assert core.label("quality.quality", "en") == "Best quality"
    assert core.label("quality.fast", "zh-TW") == "快速"
    assert core.choice_label("quality", "balanced", "ja") == "バランス"


def test_launch_flag_alone_turns_the_tech_view_on():
    core.set_tech_labels(flag=True)
    assert core.label("quality.balanced", "en") == "Balanced"


def test_dropdown_name_parses_back_in_either_view_and_language():
    for lang in LANGS:
        for tech in (False, True):
            for q in core.QUALITIES:
                shown = core.label(f"quality.{q}", lang, tech=tech)
                assert core.choice_value("quality", shown, core.QUALITIES) == q


def test_gui_switch_text_exists_in_all_languages():
    import tinglongwu.fmt  # noqa: F401

    assert all("gui_tech_labels" in i18n.MESSAGES[l] for l in LANGS)


def test_setting_round_trips_and_defaults_off(tmp_path):
    p = tmp_path / "gui.json"
    assert core.load_settings(p)["tech_labels"] is False
    s = core.load_settings(p)
    s["tech_labels"] = True
    core.save_settings(s, p)
    assert core.load_settings(p)["tech_labels"] is True
