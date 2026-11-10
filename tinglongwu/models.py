"""Local model locations. Models are never downloaded at runtime."""
from __future__ import annotations

import os
from pathlib import Path


class ModelNotFound(RuntimeError):
    pass


def models_dir(arg: str | None = None) -> Path:
    if arg:
        return Path(arg)
    if os.environ.get("TLWU_MODELS"):
        return Path(os.environ["TLWU_MODELS"])
    return Path(__file__).resolve().parent.parent / "models"


VOSK_TTS_RU_DIR = "vosk-model-tts-ru-0.7-multi"  # OPT-IN (tlwu-setup --vosk-tts-ru); keep in sync with tinglongwu_setup


def vosk_ru_installed(path: str | None = None) -> bool:
    """True if a Vosk Russian TTS model dir exists (explicit path, else the one `tlwu-setup --vosk-tts-ru` makes)."""
    return (Path(path) if path else models_dir(None) / VOSK_TTS_RU_DIR).is_dir()


def require(path: Path, what: str, hint: str) -> Path:
    if not path.exists():
        raise ModelNotFound(
            f"{what} not found at {path}.\n"
            f"tlwu never downloads models at runtime. Run once (with network):\n"
            f"  tlwu-setup {hint}"
        )
    return path
