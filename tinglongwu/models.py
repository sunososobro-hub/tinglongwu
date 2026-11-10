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


# Piper Russian voices (CC0 datasets, OHF-Voice/voice-datasets; #128). `tlwu-setup --piper-ru [dmitri|denis]`
# downloads one to models/piper-ru_RU-<voice>-medium/ (keep in sync with tinglongwu_setup.downloader.PIPER_RU).
# Never irina (CC BY-NC-ND) or ruslan (CC BY-NC-SA). Piper itself is GPL-3: it only runs in its own venv (tts_worker).
PIPER_RU_VOICES = ("dmitri", "denis")


def piper_ru_dir(voice: str) -> str:
    return f"piper-ru_RU-{voice}-medium"


def piper_onnx(path) -> Path | None:
    """The Piper voice model (.onnx with its .onnx.json next to it) inside `path` (searched recursively), or None."""
    if not path or not Path(path).is_dir():
        return None
    for f in sorted(Path(path).rglob("*.onnx")):
        if f.with_name(f.name + ".json").is_file():
            return f
    return None


def default_piper_ru(mdir: str | None = None) -> Path | None:
    """Dir of the first installed Piper Russian voice (dmitri before denis) under the models dir, or None."""
    for v in PIPER_RU_VOICES:
        d = models_dir(mdir) / piper_ru_dir(v)
        if piper_onnx(d):
            return d
    return None


def require(path: Path, what: str, hint: str) -> Path:
    if not path.exists():
        raise ModelNotFound(
            f"{what} not found at {path}.\n"
            f"tlwu never downloads models at runtime. Run once (with network):\n"
            f"  tlwu-setup {hint}"
        )
    return path


WHISPER_SIZES = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
# fallback order when the wanted model is not downloaded: best quality per speed first (large-v3-turbo is the
# recommended GPU model, README / #63), then smaller ones
WHISPER_FALLBACK = ["large-v3-turbo", "small", "medium", "base", "large-v3", "tiny"]


def whisper_installed(mdir: Path | None = None) -> list[str]:
    """Whisper sizes that exist under the models dir (a `whisper-<size>` folder)."""
    d = mdir or models_dir(None)
    return [s for s in WHISPER_SIZES if (d / f"whisper-{s}").is_dir()]


def pick_whisper(wanted: str | None, mdir: Path | None = None) -> str:
    """`wanted` if it is downloaded, else the best downloaded one; `wanted` (or 'small') when nothing is
    downloaded, so the normal 'run tlwu-setup' error still shows."""
    have = whisper_installed(mdir)
    if wanted and wanted in have:
        return wanted
    for s in WHISPER_FALLBACK:
        if s in have:
            return s
    return wanted or "small"
