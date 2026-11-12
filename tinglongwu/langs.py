"""Source-language list shared by the GUI and the menus (#58).

Owner decision 2026-11-10: the focus languages are ja, en, ru (zh is the base). They are listed first everywhere;
every other language stays selectable but is labeled "experimental". Nothing is removed or downloaded here.

The menus print `python -m tinglongwu.langs` when you press L, so the bash menu (Linux + macOS tlwu-menu), the
Windows menu and the GUI dropdown all show the same order and labels in the UI language (TLWU_UI_LANG).
"""
from __future__ import annotations

import sys

from .i18n import ui_lang

FOCUS = ("ja", "en", "ru")
BASE = "zh"
EXPERIMENTAL = ("ko", "de", "fr", "es", "it", "pt", "vi", "th", "id")
SOURCE_LANGS = ["auto", *FOCUS, BASE, *EXPERIMENTAL]

_NAMES = {
    "en": {"ja": "Japanese", "en": "English", "ru": "Russian", "zh": "Chinese", "ko": "Korean", "de": "German",
           "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese", "vi": "Vietnamese", "th": "Thai",
           "id": "Indonesian"},
    "ru": {"ja": "японский", "en": "английский", "ru": "русский", "zh": "китайский", "ko": "корейский",
           "de": "немецкий", "fr": "французский", "es": "испанский", "it": "итальянский", "pt": "португальский",
           "vi": "вьетнамский", "th": "тайский", "id": "индонезийский"},
    "zh-TW": {"ja": "日文", "en": "英文", "ru": "俄文", "zh": "中文", "ko": "韓文", "de": "德文", "fr": "法文",
              "es": "西文", "it": "義大利文", "pt": "葡萄牙文", "vi": "越南文", "th": "泰文", "id": "印尼文"},
}
_TEXT = {
    "en": {"exp": "experimental", "focus": "Languages: auto = Japanese/English/Russian (follows the speaker)",
           "exp_head": "Experimental (listen only, less tested):",
           "speak": "Meeting / speak-only support auto, ja, en, ru."},
    "ru": {"exp": "экспериментальный", "focus": "Языки: auto — японский/английский/русский (по говорящему)",
           "exp_head": "Экспериментальные (только слушать, мало проверены):",
           "speak": "Встреча и «только говорить» поддерживают auto, ja, en, ru."},
    "zh-TW": {"exp": "實驗性", "focus": "語言：auto 自動（日/英/俄，跟著說話的人）",
              "exp_head": "實驗性（只能聽、測試較少）：",
              "speak": "開會／純說只支援 auto、ja、en、ru。"},
}


def _ui(lang: str | None) -> str:
    lang = lang or ui_lang()
    return lang if lang in _TEXT else "en"


def is_valid(code: str) -> bool:
    """True only for a real source-language code (auto, ja, en, ru, zh, ...); never for menu digits like "3"."""
    return code in SOURCE_LANGS


def is_experimental(code: str) -> bool:
    return code in EXPERIMENTAL


def name(code: str, lang: str | None = None) -> str:
    return _NAMES[_ui(lang)].get(code, code)


def label(code: str, lang: str | None = None) -> str:
    """GUI dropdown entry: the code itself, plus "(experimental)" for non-focus languages."""
    return f"{code} ({_TEXT[_ui(lang)]['exp']})" if is_experimental(code) else code


def labels(lang: str | None = None) -> list[str]:
    return [label(c, lang) for c in SOURCE_LANGS]


def from_label(text: str) -> str:
    """Language code from a dropdown entry (also accepts a plain code)."""
    return (text or "").strip().split(" ", 1)[0] or "auto"


def display_name(code: str, lang: str | None = None) -> str:
    """Menu header name: "Korean (experimental)" for non-focus languages, plain name otherwise."""
    ui = _ui(lang)
    n = name(code, ui)
    return f"{n} ({_TEXT[ui]['exp']})" if is_experimental(code) else n


def menu_help(lang: str | None = None) -> str:
    """What the menus print for L: focus languages first, then the experimental ones, each marked."""
    ui = _ui(lang)
    tx = _TEXT[ui]
    sep = "、" if ui == "zh-TW" else ", "
    focus = sep.join(f"{c} {name(c, ui)}" for c in FOCUS)
    exp = sep.join(f"{c} {name(c, ui)}" for c in EXPERIMENTAL)
    return f"{tx['focus']}\n  {focus}\n{tx['exp_head']}\n  {exp}\n{tx['speak']}"


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows console (menu sets PYTHONUTF8 too)
    except (AttributeError, ValueError):
        pass
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--name"] and len(argv) == 2:  # menu header: python -m tinglongwu.langs --name ko
        print(display_name(argv[1]))
    elif argv[:1] == ["--check"] and len(argv) == 2:  # menu: exit 0 only for a valid source-language code
        return 0 if is_valid(argv[1]) else 1
    else:
        print(menu_help())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
