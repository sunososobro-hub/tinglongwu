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
    for ui, mark in (("zh-TW", "（需下載）"), ("en", "(not downloaded)")):
        monkeypatch.setattr(core, "_ui", lambda ui=ui: ui)
        monkeypatch.setattr("tinglongwu.models.whisper_installed", lambda mdir=None: set())
        labels = core.model_labels()
        assert all(mark in l for l in labels)
        assert ("not downloaded" in labels[0]) == (ui == "en") and ("需下載" in labels[0]) == (ui == "zh-TW")
        assert [core.model_from_label(l) for l in labels] == core.MODELS
