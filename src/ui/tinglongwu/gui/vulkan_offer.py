"""#1775: offer the Vulkan pack + whisper.cpp model inside the GUI (AMD / Intel iGPU), one click, no command line.

No tkinter and no network here: `detect` / `should_offer` decide, `install_cmds` builds the setup children (the
networked workers live in tinglongwu_setup and run only after the user clicked Install), `probe` reports what the app
will use after the install. Linux uses the `-linux-` pack asset (vulkan_pack.asset_format picks it, #1198).
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

DISMISS_KEY = "vulkan_offer_never_ask"  # settings key: the user pressed "Don't ask again" on the Vulkan banner
MODEL_SIZE = "small"  # whisper.cpp ggml model the one-click install fetches (the default listening size)
UNAVAILABLE, MISSING, STALE, INSTALLED = "unavailable", "missing", "stale", "installed"
ROW_NAME = "vulkan-pack"
INSTALLING = threading.Event()  # one Vulkan install at a time


def supported(platform: str | None = None) -> bool:
    """The release ships a Vulkan pack for Windows and Linux only (macOS has none)."""
    return (platform or sys.platform) == "win32" or (platform or sys.platform).startswith("linux")


def state(vulkan_present: bool, pack_folder: bool, pack_ok: bool, model_ready: bool) -> str:
    """unavailable (no Vulkan device) / missing (no vulkan-pack/) / stale (vulkan-pack/ there but not usable by this
    app version, or the whisper.cpp model / build is missing) / installed."""
    if not vulkan_present:
        return UNAVAILABLE
    if not pack_folder:
        return MISSING
    return INSTALLED if pack_ok and model_ready else STALE


def should_offer(st: str, nvidia: bool, dismissed: bool, offline: bool) -> bool:
    """Banner only when Vulkan is there and the pack is missing or stale; an NVIDIA machine gets the CUDA offer instead."""
    return st in (MISSING, STALE) and not nvidia and not dismissed and not offline


def gate_for_nvidia(st: str, nvidia: bool) -> str:
    """#1780: an NVIDIA machine gets the CUDA offer only; never a Vulkan banner (also on Linux where the CUDA pack is absent)."""
    return UNAVAILABLE if nvidia else st


def install_blocked(gpu_busy: bool, downloading: bool, bg_active: bool) -> bool:
    """#1780: refuse the Vulkan install while another install or any download (foreground / background) is running."""
    return bool(gpu_busy or INSTALLING.is_set() or downloading or bg_active)


def defer_bg_defaults() -> bool:
    """#1780: the background default-model download waits while the Vulkan install runs (one manifest writer)."""
    return INSTALLING.is_set()


def cpu_backends(lines: list[str]) -> list[str]:
    """#1780: names ("speech", "translation") that the final `vulkan probe:` line says stay on the CPU."""
    for ln in reversed(lines):
        if ln.startswith("vulkan probe:"):
            if "skipped" in ln:
                return ["speech", "translation"]
            out = []
            if "ASR cpu" in ln:
                out.append("speech")
            if "MT cpu" in ln:
                out.append("translation")
            return out
    return []


def detect(mdir=None, size: str = MODEL_SIZE, platform: str | None = None, present=None, pack=None,
           folder=None, ready=None) -> str:
    """state() for this machine. The probes are injectable (tests pass fakes)."""
    if not supported(platform):
        return UNAVAILABLE
    from .. import asr_vulkan, mt_backend
    from ..models import models_dir

    md = models_dir(str(mdir) if mdir else None)
    if present is None:
        present = mt_backend.vulkan_device_present
    if not present():
        return UNAVAILABLE
    if folder is None:
        folder = lambda: (mt_backend.APP_ROOT / mt_backend.VULKAN_PACK_NAME).is_dir() or _env_pack_dir().is_dir()  # noqa: E731
    p = (pack or (lambda: mt_backend.vulkan_pack_dir(warn=lambda m: None)))()  # stale pack: None, no stderr noise
    rdy = (ready or (lambda: asr_vulkan.vulkan_ready(md, size, p)))() if p is not None else False
    return state(True, bool(folder()), p is not None, rdy)


def _env_pack_dir() -> Path:
    import os

    return Path(os.environ.get("TLWU_VULKAN_PACK") or "/nonexistent-tlwu-vulkan-pack")


def install_cmds(mdir=None, size: str = MODEL_SIZE, bundled: bool | None = None) -> list[list[str]]:
    """The same setup children as `tlwu-setup --vulkan-pack` and `--whisper-cpp <size>` (the pack matches this app's
    version: tlwu.exe adds --app-version itself, from source tinglongwu_setup reads it)."""
    from .core import download_cmd

    base = download_cmd(size, mdir, bundled)
    i = base.index("--whisper")
    return [[*base[:i], "--vulkan-pack", *base[i + 2:]], [*base[:i], "--whisper-cpp", size, *base[i + 2:]]]


def probe(mdir=None, size: str = MODEL_SIZE) -> dict:
    """What the app will run on after the install (readiness, not timing): {"mt": bool, "asr": bool}."""
    from .. import asr_vulkan, mt_backend
    from ..models import models_dir

    pack = mt_backend.vulkan_pack_dir(warn=lambda m: None)
    return {"mt": mt_backend.vulkan_available(), "asr": asr_vulkan.vulkan_ready(models_dir(str(mdir) if mdir else None), size, pack)}


def run_install(run, put, cmds, mdir=None, size: str = MODEL_SIZE, probe_fn=probe) -> int:
    """Worker thread: run each child in order (stop at the first failure), forward its lines, then probe. `put` gets
    ("pack_line", text) and one final ("pack_done", ROW_NAME, rc); INSTALLING is released when it really stopped."""
    rc = 0
    try:
        for cmd in cmds:
            rc = run(ROW_NAME, lambda ev: ev[0] != "pack_done" and put(ev), cmd=cmd)
            if rc != 0:
                break
        if rc == 0:
            try:
                r = probe_fn(mdir, size)
                put(("pack_line", "vulkan probe: ASR " + ("vulkan" if r["asr"] else "cpu") + ", MT " + ("vulkan" if r["mt"] else "cpu")))
            except Exception as e:  # noqa: BLE001 - the probe is informative only
                put(("pack_line", f"vulkan probe: skipped ({type(e).__name__})"))
        put(("pack_done", ROW_NAME, rc))
        return rc
    finally:
        INSTALLING.clear()
