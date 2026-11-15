"""#967: offer the Kokoro voice at the moment speak needs it. No tkinter here (the GUI passes the answers in).

The install itself runs in the tlwu-setup child (`--talk`, same step as the CLI): this module only decides whether to
ask and words the question. The runtime package never downloads anything itself."""
from __future__ import annotations

from pathlib import Path

VOICE_PACK = "voice"  # name shown in the progress line and used as the pack_retry key
RU_PACK = "voice-ru"  # Russian voice (Piper): its own progress name / pack_retry key
RU_MB = 80  # the dmitri voice (~60 MB) + the piper-tts wheel
VOICE_MB = 400  # rough size of the Kokoro venv + model, only used to word the question
TEXT = {
    "en": "Speaking into the meeting needs the read-aloud voice (Kokoro, about {mb} MB). Install it now? Needs internet once, "
          "works offline afterwards.",
    "ru": "Для голоса во встречу нужен голос озвучки (Kokoro, около {mb} МБ). Установить сейчас? Интернет нужен один раз, "
          "потом работает офлайн.",
    "zh-TW": "把你說的話念進會議需要朗讀語音（Kokoro，約 {mb} MB）。現在安裝嗎？只需連網一次，之後可離線使用。",
}
RU_TEXT = {
    "en": "Speaking Russian needs the Russian read-aloud voice (about {mb} MB). Install it now? Needs internet once, "
          "works offline afterwards.",
    "ru": "Для русской озвучки нужен русский голос (около {mb} МБ). Установить сейчас? Интернет нужен один раз, "
          "потом работает офлайн.",
    "zh-TW": "要用俄語把你說的話念出來，需要俄語朗讀語音（約 {mb} MB）。現在安裝嗎？只需連網一次，之後可離線使用。",
}
TITLE = {"en": "Install voice", "ru": "Установка голоса", "zh-TW": "安裝朗讀語音"}


def voice_missing(mdir, tts_python: str | None = None, tts_model: str | None = None) -> bool:
    """True when the Kokoro venv python or the model dir is not there (what voice.py needs to start the voice)."""
    from .. import voice  # module import on purpose: tests/test_reverse_en.py greps sources for the GPL package name

    py, model = voice.kokoro_paths(Path(mdir), tts_python, tts_model)
    return not (Path(py).is_file() and Path(model).is_dir() and any(Path(model).iterdir()))


def is_ru(run: dict) -> bool:
    """True when this run speaks Russian (the other side's language; zh-TW style codes use their base)."""
    from .. import voice

    return voice.voice_lang(run.get("target") or "") == "ru"


def piper_engine_installed(py: str) -> bool:
    """True when the TTS venv of python `py` has the piper package (file check, no process started)."""
    root = Path(py).parent.parent  # <venv>/Scripts/python.exe or <venv>/bin/python
    return any(c.is_dir() for c in [root / "Lib" / "site-packages" / "piper", *root.glob("lib/python*/site-packages/piper")])


def piper_missing(mdir, tts_python: str | None = None, tts_model: str | None = None) -> bool:
    """True when the Russian voice cannot start: no TTS venv python, no piper-tts in it, or no Piper voice files."""
    from .. import voice
    from ..models import default_piper_ru, piper_onnx

    py, _ = voice.kokoro_paths(Path(mdir), tts_python, None)
    model = piper_onnx(tts_model) if tts_model else default_piper_ru(str(mdir))
    return not (Path(py).is_file() and piper_engine_installed(py) and model)


def offer_needed(run: dict, mdir, tts_python: str | None = None, tts_model: str | None = None) -> bool:
    """True when this Start speaks aloud (voice on) and its voice is not installed (Kokoro; Piper for Russian): the
    GUI then offers the one-click install instead of starting with a red line."""
    if not run.get("voice"):
        return False
    if is_ru(run):
        return piper_missing(mdir, tts_python, tts_model)
    return voice_missing(mdir, tts_python, tts_model)


def pack_name(run: dict) -> str:
    return RU_PACK if is_ru(run) else VOICE_PACK


def dialog_text(ui_lang: str, ru: bool = False) -> str:
    table = RU_TEXT if ru else TEXT
    return table.get(ui_lang, table["en"]).format(mb=RU_MB if ru else VOICE_MB)


def title_text(ui_lang: str) -> str:
    return TITLE.get(ui_lang, TITLE["en"])


NOTICE = {
    "en": "Russian voice is not downloaded: subtitles only.",
    "ru": "Русский голос не загружен: только субтитры.",
    "zh-TW": "俄文語音未下載，只能看字幕。",
}
NOTICE_BTN = {"en": "Download", "ru": "Скачать", "zh-TW": "下載"}


def notice_text(ui_lang: str) -> str:
    return NOTICE.get(ui_lang, NOTICE["en"])


def notice_button(ui_lang: str) -> str:
    return NOTICE_BTN.get(ui_lang, NOTICE_BTN["en"])


def ru_selected(my_lang: str | None, other_lang: str | None) -> bool:
    """True when either picker (my language / the other side's) is Russian: the dropdown check of the Russian voice."""
    from .. import voice

    return "ru" in (voice.voice_lang(my_lang or ""), voice.voice_lang(other_lang or ""))
