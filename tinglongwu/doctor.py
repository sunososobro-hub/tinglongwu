"""`tlwu doctor` (#313 part 1): one line per setup item a meeting needs, each with a fix when it is not OK.

Checks: speech model (Whisper), translation model (Hy-MT2), voice model + package for the language the other side
hears, the virtual cable the meeting app records from, the current default output device, GPU/VRAM.
Every probe is injectable, so the checks run in tests without devices, models or a GPU. Nothing is downloaded.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

OK, WARN, FAIL = "ok", "warn", "FAIL"
VOICE_LANGS = ("ru", "en", "ja", "zh")
KOKORO_FILES = ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin")  # keep in sync with tinglongwu_setup.downloader


@dataclass
class Check:
    name: str
    status: str  # OK / WARN / FAIL
    detail: str
    fix: str = ""

    def line(self) -> str:
        s = f"[{self.status:>4}] {self.name}: {self.detail}"
        return s + (f"\n         fix: {self.fix}" if self.fix and self.status != OK else "")


def check_whisper(mdir: Path) -> Check:
    from .models import whisper_installed

    have = whisper_installed(mdir)
    if have:
        return Check("speech model", OK, "Whisper " + ", ".join(have))
    return Check("speech model", FAIL, f"no Whisper model under {mdir}", "tlwu-setup --whisper small")


def check_mt(mdir: Path) -> Check:
    from .hymt import hymt_path

    p = hymt_path(mdir)
    if p.exists():
        return Check("translation model", OK, f"Hy-MT2 ({p.name})")
    return Check("translation model", FAIL, f"Hy-MT2 missing ({p})", "tlwu-setup --hymt")


def check_voice(lang: str, mdir: Path, has_module: Callable[[str], bool], kokoro_python: Path) -> Check:
    """Voice for `lang` (the language the other side hears): model files and the package that speaks them."""
    from .models import default_piper_ru, default_vosk_ru, models_dir

    name = f"voice ({lang})"
    if lang == "ru":
        if default_piper_ru(str(mdir)):
            return Check(name, OK, "Piper Russian voice (runs in its own venv)")
        if default_vosk_ru(str(mdir)):
            if has_module("vosk_tts"):
                return Check(name, OK, "Vosk Russian voice + vosk-tts package")
            return Check(name, FAIL, "Vosk Russian model found but the vosk-tts package is not installed",
                         "tlwu-setup --vosk-tts-ru")
        return Check(name, FAIL, "no Russian voice model: the other side hears nothing",
                     "tlwu-setup --piper-ru (or --vosk-tts-ru)")
    d = models_dir(str(mdir)) / "kokoro"
    missing = [f for f in KOKORO_FILES if not (d / f).is_file()]
    if missing:
        return Check(name, FAIL, f"Kokoro voice files missing ({', '.join(missing)})", "tlwu-setup --talk")
    # kokoro-onnx bundles GPL espeak: it lives in its own venv and is never imported here (test_reverse_en)
    if kokoro_python.is_file():
        return Check(name, OK, f"Kokoro voice + its venv ({kokoro_python})")
    return Check(name, FAIL, f"Kokoro voice files found but its venv is missing ({kokoro_python})",
                 "tlwu-setup --talk")


def check_cable(platform: str, speakers: list[str] | None) -> Check:
    """The virtual device the meeting app records from (#311 picks it the same way)."""
    from .reverse.vmic import WIN_CABLE, pick_win_cable

    if platform.startswith("linux"):
        return Check("virtual mic", OK, "created when speak mode starts (select 'tlwu_mic' in the meeting app)")
    if speakers is None:
        return Check("virtual mic", WARN, "could not list output devices", "check the sound settings")
    if platform == "darwin":
        if any("blackhole" in s.lower() for s in speakers):
            return Check("virtual mic", OK, "BlackHole found (select it as the microphone in the meeting app)")
        return Check("virtual mic", FAIL, "BlackHole not installed: the meeting app cannot hear the voice",
                     "install BlackHole 16ch (https://existential.audio/blackhole/)")
    dev = pick_win_cable(speakers)
    if dev:
        return Check("virtual mic", OK, f"{dev!r} found: in the meeting app set Microphone = 'CABLE Output'")
    return Check("virtual mic", FAIL, f"no '{WIN_CABLE}' device: the meeting app cannot hear the voice",
                 "install VB-CABLE (https://vb-audio.com/Cable/) and restart")


def check_output(default_speaker: str | None) -> Check:
    if not default_speaker:
        return Check("default output", WARN, "no default output device found",
                     "plug in a headset or pick a speaker in the sound settings")
    if "cable input" in default_speaker.lower():
        return Check("default output", WARN, f"{default_speaker!r} is the default: you will not hear the meeting",
                     "set your headset or speakers as the default output device")
    return Check("default output", OK, f"{default_speaker} (listen mode captures this; restart after changing it)")


def check_gpu(gpu: tuple[str | None, int | None]) -> Check:
    name, mib = gpu
    if not name:
        return Check("GPU", WARN, "no NVIDIA GPU: runs on CPU (slower; use a small model)")
    if mib is not None and mib < 4096:
        return Check("GPU", WARN, f"{name}, {mib} MiB VRAM: under 4 GB, larger models may not fit")
    return Check("GPU", OK, f"{name}, {mib} MiB VRAM")


# the venv `tlwu-setup --talk` makes (tinglongwu_setup.talk.default_venv; not imported: the runtime package has no
# setup/network code, tests/test_no_network_code.py)
KOKORO_VENV = Path.home() / "tts-kokoro"


def _has_module(m: str) -> bool:
    """True if the Vosk voice package imports in this Python (the TTS worker default, #311). Static imports only."""
    try:
        if m == "vosk_tts":
            import vosk_tts  # noqa: F401
        else:
            return False
        return True
    except Exception:  # noqa: BLE001 - missing or broken install: both mean "not usable"
        return False


def _speakers() -> tuple[list[str] | None, str | None]:
    try:
        import soundcard as sc

        names = [s.name for s in sc.all_speakers()]
        d = sc.default_speaker()
        return names, (d.name if d is not None else None)
    except Exception:  # noqa: BLE001 - no audio backend / no devices: reported as a warning
        return None, None


def run_checks(lang: str = "ru", mdir: Path | None = None, *, platform: str = sys.platform,
               speakers: Callable[[], tuple[list[str] | None, str | None]] | None = None,
               gpu: Callable[[], tuple[str | None, int | None]] | None = None,
               has_module: Callable[[str], bool] | None = None,
               kokoro_python: Path | None = None) -> list[Check]:
    from .models import models_dir

    mdir = models_dir(str(mdir) if mdir else None)
    if gpu is None:
        from .hwprobe import gpu_info as gpu
    if has_module is None:
        has_module = _has_module
    if kokoro_python is None:
        kokoro_python = KOKORO_VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    names, default = (speakers or _speakers)()
    return [check_whisper(mdir), check_mt(mdir), check_voice(lang, mdir, has_module, kokoro_python),
            check_cable(platform, names), check_output(default), check_gpu(gpu())]


def summary(checks: list[Check]) -> str:
    bad = sum(c.status == FAIL for c in checks)
    return "ready for a meeting" if not bad else f"{bad} problem(s): run the fix line(s) above, then check again"


def main(argv=None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="tlwu doctor", description="Check that this machine is ready for a meeting.")
    p.add_argument("--to", default="ru", choices=VOICE_LANGS, help="language the other side hears (default ru)")
    p.add_argument("--models-dir", default=None)
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    checks = run_checks(a.to, Path(a.models_dir) if a.models_dir else None)
    for c in checks:
        print(c.line())
    print(summary(checks))
    return 1 if any(c.status == FAIL for c in checks) else 0
