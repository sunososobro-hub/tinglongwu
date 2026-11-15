"""#766: `tlwu-setup --default-listen` installs only the default listen path (en -> zh-TW) and nothing else.

Part of tinglongwu_setup (the networked package); does not import `tinglongwu`. Never runs pip.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

DEFAULT_LISTEN_SOURCE = "en"
DEFAULT_LISTEN_TARGET = "zh-TW"
DEFAULT_LISTEN_WHISPER = "small"  # #770: smallest size that passes the eval gate; tiny/base fail en->zh (eval/whisper_size_770.json)
DEFAULT_LISTEN_MT = "hymt2-1.8b"
DEFAULT_LISTEN_KEYS = (f"whisper-{DEFAULT_LISTEN_WHISPER}", DEFAULT_LISTEN_MT)  # models/manifest.json keys
MB = 1024 * 1024
# #771: download size (MB) the installer wizard shows for this path: whisper-small ~0.5 GB + Hy-MT2 1.8B GGUF ~1.1 GB
# (mirrors tlwu_app.tiers.SIZES, which this package must not import). build_installer.py passes it to the .iss.
DEFAULT_LISTEN_DOWNLOAD_MB = 1600


VOICE_NOTE = ("NOTE voice not installed: this setup installs listening only. Speaking (我來說) needs the Kokoro voice: "
              "press 我來說 in the app for a one-click install, or run `tlwu-setup --talk`.")


def voice_installed(mdir: Path, venv: Path | None = None) -> bool:
    """True when the pinned Kokoro model files are in mdir AND the Kokoro venv (`tlwu-setup --talk`) has its python.
    #967: model files alone are not enough (the owner's machine had the model but no venv, so speak stayed silent)."""
    from . import talk

    d = mdir / "kokoro"
    files = all((d / n).is_file() for n in ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin"))
    return files and talk.venv_python(venv or talk.default_venv()).is_file()


def _say(line: str) -> None:
    """Flush every line: the wizard polls install.log while this runs, and a redirected stdout is block-buffered."""
    print(line, flush=True)


def _dir_bytes(d: Path) -> int:
    return sum(p.stat().st_size for p in d.rglob("*") if p.is_file()) if d.is_dir() else 0


def run(mdir: Path, getters: dict | None = None, out=_say, venv: Path | None = None) -> int:
    """Fetch exactly DEFAULT_LISTEN_KEYS into mdir. `getters` maps key -> fn(man, mdir) (tests inject a fake)."""
    from . import downloader

    getters = getters or {
        f"whisper-{DEFAULT_LISTEN_WHISPER}": lambda man, d: downloader.get_whisper(man, d, DEFAULT_LISTEN_WHISPER),
        DEFAULT_LISTEN_MT: downloader.get_hymt,
    }
    mdir.mkdir(parents=True, exist_ok=True)
    mf = mdir / "manifest.json"
    man = json.loads(mf.read_text("utf-8")) if mf.exists() else {}
    t0, total = time.monotonic(), len(DEFAULT_LISTEN_KEYS)
    before = sum(_dir_bytes(mdir / k) for k in DEFAULT_LISTEN_KEYS)
    for i, key in enumerate(DEFAULT_LISTEN_KEYS, 1):
        out(f"[{i}/{total}] {key}")
        getters[key](man, mdir)
        mf.write_text(json.dumps(man, indent=1), "utf-8")
    after = sum(_dir_bytes(mdir / k) for k in DEFAULT_LISTEN_KEYS)
    if not voice_installed(mdir, venv):  # #967: say it, so a user who later presses 我來說 is not surprised
        out(VOICE_NOTE)
    out(f"READY default-listen seconds={time.monotonic() - t0:.1f} download_mb={max(0, after - before) // MB}")
    return 0
