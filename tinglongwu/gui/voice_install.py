"""#967: offer the Kokoro voice at the moment speak needs it. No tkinter here (the GUI passes the answers in).

The install itself runs in the tlwu-setup child (`--talk`, same step as the CLI): this module only decides whether to
ask and words the question. The runtime package never downloads anything itself."""
from __future__ import annotations

from pathlib import Path

VOICE_PACK = "voice"  # name shown in the progress line and used as the pack_retry key
VOICE_MB = 400  # rough size of the Kokoro venv + model, only used to word the question
TEXT = {
    "en": "Speaking into the meeting needs the read-aloud voice (Kokoro, about {mb} MB). Install it now? Needs internet once, "
          "works offline afterwards.",
    "ru": "Для голоса во встречу нужен голос озвучки (Kokoro, около {mb} МБ). Установить сейчас? Интернет нужен один раз, "
          "потом работает офлайн.",
    "zh-TW": "把你說的話念進會議需要朗讀語音（Kokoro，約 {mb} MB）。現在安裝嗎？只需連網一次，之後可離線使用。",
}
TITLE = {"en": "Install voice", "ru": "Установка голоса", "zh-TW": "安裝朗讀語音"}


def voice_missing(mdir, tts_python: str | None = None, tts_model: str | None = None) -> bool:
    """True when the Kokoro venv python or the model dir is not there (what voice.py needs to start the voice)."""
    from .. import voice  # module import on purpose: tests/test_reverse_en.py greps sources for the GPL package name

    py, model = voice.kokoro_paths(Path(mdir), tts_python, tts_model)
    return not (Path(py).is_file() and Path(model).is_dir() and any(Path(model).iterdir()))


def offer_needed(run: dict, mdir, tts_python: str | None = None, tts_model: str | None = None) -> bool:
    """True when this Start speaks aloud (voice on) and the Kokoro voice is not installed: the GUI then offers the
    one-click install instead of starting with a red line."""
    return bool(run.get("voice")) and voice_missing(mdir, tts_python, tts_model)


def dialog_text(ui_lang: str) -> str:
    return TEXT.get(ui_lang, TEXT["en"]).format(mb=VOICE_MB)


def title_text(ui_lang: str) -> str:
    return TITLE.get(ui_lang, TITLE["en"])
