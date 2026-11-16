"""#985: Hindi is a selectable experimental source language."""
import subprocess
import sys

from tinglongwu import langs


def test_hi_selectable_and_experimental():
    assert "hi" in langs.SOURCE_LANGS
    assert langs.is_valid("hi")
    assert langs.is_experimental("hi")


def test_hi_names_in_every_ui_language():
    assert langs.name("hi", "en") == "Hindi"
    assert langs.name("hi", "ru") == "хинди"
    assert langs.name("hi", "zh-TW") == "印地文"
    assert langs.from_label(langs.label("hi", "en")) == "hi"
    assert langs.label("hi", "en") == "Hindi (experimental)"


def test_menu_lists_hindi_under_experimental():
    for ui, nm in (("en", "Hindi"), ("ru", "хинди"), ("zh-TW", "印地文")):
        out = subprocess.run([sys.executable, "-m", "tinglongwu.langs"], capture_output=True, text=True,
                             encoding="utf-8", env={**__import__("os").environ, "TLWU_UI_LANG": ui}).stdout
        exp_part = out.split("\n", 2)[2]
        assert f"hi {nm}" in exp_part
