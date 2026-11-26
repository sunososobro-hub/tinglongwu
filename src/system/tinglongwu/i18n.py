"""Runtime messages (network guard, report prompts) in the UI language. The text lives in the i18n_<topic>.py modules
(#1917); adding a language = adding its block to each of them and to LANGS (and scripts/i18n/<lang>.sh / .psd1 for
the menus). UI language: env TLWU_UI_LANG > config ui_lang > system > en.
Only UI text is translated; subtitle/translation target languages are unrelated to this module."""
from __future__ import annotations

from . import diag as _diag

import os
import re
import subprocess
import sys

from . import userdirs
from . import (i18n_addons, i18n_gpu, i18n_gui, i18n_gui_menu, i18n_gui_speak, i18n_gui_topics, i18n_gui_update,
               i18n_runtime)

DEFAULT = "en"
SILENT_CLEAR = "@@silent-clear@@"  # notice payload: the listened sound is back, drop the "10 s silence" hint

# Messages live in one module per topic (#1917: i18n.py was a top-3 merge-conflict file). Each module holds
# {lang: {key: text}}; they are merged here into one table per language, in TOPICS order.

TOPICS = (i18n_runtime, i18n_gpu, i18n_addons, i18n_gui_menu, i18n_gui_update, i18n_gui_topics, i18n_gui_speak, i18n_gui)
LANGS = ("en", "ru", "zh-TW", "ja")
MESSAGES: dict[str, dict[str, str]] = {lang: {} for lang in LANGS}
for _topic in TOPICS:
    for _lang, _table in _topic.MESSAGES.items():
        MESSAGES[_lang].update(_table)
del _topic, _lang, _table


def normalize(tag: str | None) -> str:
    """Locale tag (ru_RU.UTF-8, ja_JP, zh-Hant-TW, en-US...) -> a supported UI language. zh-CN also uses the zh-TW text."""
    t = (tag or "").strip().lower().replace("_", "-")
    if t.startswith("ru"):
        return "ru"
    if re.match(r"ja(?![a-z])", t):  # ja, ja-JP, ja_JP.UTF-8; not jv / jav
        return "ja"
    if t.startswith("zh"):
        return "zh-TW"
    return DEFAULT


def _system_tag() -> str:
    if sys.platform == "win32":
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):
                return buf.value
        except Exception:  # noqa: BLE001
            _diag.swallowed(__name__)
            pass
        return ""
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True,
                                 timeout=3).stdout
            m = re.search(r'"?([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)*)"?', out.split("(", 1)[-1])
            if m:
                return m.group(1)
        except Exception:  # noqa: BLE001
            _diag.swallowed(__name__)
            pass
    for k in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(k):
            return os.environ[k]
    return ""


_cached: str | None = None
_override: str | None = None  # set by the GUI language dropdown; beats env/config for this process


def set_ui_lang(lang: str, persist: bool = True) -> str:
    """Switch the UI language now (GUI dropdown) and remember it in the config file."""
    global _override
    _override = normalize(lang)
    if persist:
        try:
            userdirs.write_config_value("ui_lang", _override)
        except OSError:
            pass
    return _override


def ui_lang() -> str:
    global _cached
    if _override:
        return _override
    env = os.environ.get("TLWU_UI_LANG", "").strip()
    if env and env != "auto":
        return normalize(env)
    cfg = userdirs.read_config().get("ui_lang", "").strip()
    if cfg and cfg != "auto":
        return normalize(cfg)
    if _cached is None:
        _cached = normalize(_system_tag())
    return _cached


def t(key: str, **kw) -> str:
    table = MESSAGES.get(ui_lang(), {})
    s = table.get(key) or MESSAGES[DEFAULT][key]
    return s.format(**kw) if kw else s
