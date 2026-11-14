"""#884: the GUI shows language / option names, never raw codes (zh-tw, auto, system ...)."""
import pytest

from tinglongwu import langs
from tinglongwu.gui import core

BAD = ("-tw", "-cn", "taiwan", "china", "台灣", "中國")


@pytest.mark.parametrize("ui", ["zh-TW", "en"])
def test_pair_label_targets_have_no_region(ui):
    for target in core.TARGETS:
        for src in langs.SOURCE_LANGS:
            text = f"{langs.plain_label(src, ui)}→{core.choice_label('target', target, ui)}"
            assert not any(b in text.lower() for b in BAD), text
            assert text.split("→")[0] != src


def test_pair_label_examples():
    assert f"{langs.plain_label('en', 'zh-TW')}→{core.choice_label('target', 'zh-tw', 'zh-TW')}" == "英文→繁體中文"
    assert core.choice_label("target", "zh-cn", "zh-TW") == "简体中文"
    assert core.choice_label("target", "zh-tw", "en") == "Traditional Chinese"
    assert langs.plain_label("auto", "zh-TW") == "自動" and langs.plain_label("auto", "en") == "Auto"


@pytest.mark.parametrize("ui", ["zh-TW", "en"])
def test_pair_label_choices_hide_raw_codes_and_round_trip(ui):
    for kind, values in (("target", core.TARGETS), ("quality", core.QUALITIES), ("source", core.SOURCES),
                         ("preset", core.PRESETS), ("engine", ["auto"])):
        for v in values:
            shown = core.choice_label(kind, v, ui)
            assert shown != v, (kind, v)
            assert core.choice_value(kind, shown, values) == v
            assert not any(b in shown.lower() for b in BAD)
    for c in langs.SOURCE_LANGS:
        assert langs.from_label(langs.label(c, ui)) == c


def test_pair_label_model_marks_one_language(monkeypatch):
    # #739: a missing model downloads on the first Start, so no "not downloaded" mark in any UI language;
    # labels saved with an old mark (#884 per-language or the pre-#884 bilingual one) still parse.
    for ui, mark in (("zh-TW", "（需下載）"), ("en", " (not downloaded)"), ("ru", " (нужна загрузка)")):
        monkeypatch.setattr(core, "_ui", lambda ui=ui: ui)
        monkeypatch.setattr("tinglongwu.models.whisper_installed", lambda mdir=None: set())
        labels = core.model_labels()
        assert not any(m.strip() in l for l in labels for m in core._MARK_MISSING.values())
        assert [core.model_from_label(l) for l in labels] == core.MODELS
        assert [core.model_from_label(m + mark) for m in core.MODELS] == core.MODELS
    assert core.model_from_label("small" + core._MARK_MISSING_OLD) == "small"


def _px(text):  # fake font: 6 px per character
    return 6 * len(text)


def test_pair_text_one_line_when_it_fits():
    assert core.pair_text("auto", "en", _px, "en") == "Auto→English"
    assert core.pair_text("en", "zh-tw", _px, "zh-TW") == "英文→繁體中文"


def test_pair_text_long_names_go_to_two_lines_never_codes():
    # "English→Traditional Chinese" = 27 chars = 162 px > 120; "Traditional Chinese" = 114 px fits line 2
    assert core.pair_text("en", "zh-tw", _px, "en") == "English →\nTraditional Chinese"
    assert core.pair_text("auto", "zh-cn", _px, "en") == "Auto →\nSimplified Chinese"


def test_pair_text_too_wide_even_on_two_lines_uses_the_short_script_name():
    # ru "традиционный китайский" = 22 chars = 132 px > 120 -> "китайский (трад.)" (17 chars = 102 px)
    text = core.pair_text("auto", "zh-tw", _px, "ru")
    assert text == "Авто →\nкитайский (трад.)"
    assert max(_px(line) for line in text.split("\n")) <= core.PAIR_MAX_PX


@pytest.mark.parametrize("ui", ["zh-TW", "en", "ru"])
def test_pair_text_has_no_region_or_code(ui):
    for target in core.TARGETS:
        for src in langs.SOURCE_LANGS:
            text = core.pair_text(src, target, _px, ui)
            assert not any(b in text.lower() for b in BAD), text
            assert target not in text.split("→")[-1].strip(), text
