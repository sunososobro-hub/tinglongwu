"""#766: `tlwu-setup --default-listen` installs only the default listen path (en -> zh-TW) and nothing else.

Part of tinglongwu_setup (the networked package); does not import `tinglongwu`. Never runs pip.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

DEFAULT_LISTEN_SOURCE = "en"
DEFAULT_LISTEN_TARGET = "zh-TW"
DEFAULT_LISTEN_WHISPER = "small"  # today's CPU-path value from install.ps1; the eval child (#752 part 6) may lower it
DEFAULT_LISTEN_MT = "hymt2-1.8b"
DEFAULT_LISTEN_KEYS = (f"whisper-{DEFAULT_LISTEN_WHISPER}", DEFAULT_LISTEN_MT)  # models/manifest.json keys
MB = 1024 * 1024


def _dir_bytes(d: Path) -> int:
    return sum(p.stat().st_size for p in d.rglob("*") if p.is_file()) if d.is_dir() else 0


def run(mdir: Path, getters: dict | None = None, out=print) -> int:
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
    out(f"READY default-listen seconds={time.monotonic() - t0:.1f} download_mb={max(0, after - before) // MB}")
    return 0
