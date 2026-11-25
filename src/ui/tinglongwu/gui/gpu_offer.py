"""#1525: offer the GPU speed-up pack inside the app (banner + Add-ons row), no command line.

No tkinter and no network here: `pack_state` / `should_offer` decide, `gpu_pack_cmd` builds the `--gpu-pack` setup child
(the networked worker lives in tinglongwu_setup and runs only after the user clicked Install).
"""
from __future__ import annotations

import os
import re
import sys
import threading
from pathlib import Path

DISMISS_KEY = "gpu_pack_never_ask"  # settings key: the user pressed "Don't ask again"
AUTO_OFF_KEY = "gpu_pack_auto_off"  # #1736: the user cancelled the background download; never auto-start it again
VERSION_FILE = "pack_version.txt"  # written by tinglongwu_setup.gpu_pack.run after a successful install
GPU_PACK_SIZE = "1.5 GB"

UNAVAILABLE, MISSING, OLD, INSTALLED = "unavailable", "missing", "old", "installed"
VULKAN = "vulkan"  # #1772: no NVIDIA GPU, but a Vulkan GPU (AMD / Intel iGPU); not offered the CUDA pack
NO_PACK_PREFIX = "gpu pack: this release has no GPU pack"  # == tinglongwu_setup.gpu_pack.NO_PACK_NOTE's start
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[.+-][0-9A-Za-z.+-]*)?$")  # 0.4.10, 0.4.10.dev3, 0.4.10+g1a2b
INSTALLING = threading.Event()  # one GPU-pack install at a time across the banner and every Add-ons window


def installed_version(pack: Path | None) -> str | None:
    """Release version the installed pack came from (`pack_version.txt`); None when unknown or no pack."""
    if pack is None:
        return None
    try:
        ver = (Path(pack) / VERSION_FILE).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):  # ValueError: UnicodeDecodeError of a garbled marker = unknown
        return None
    return ver if VERSION_RE.match(ver) else None


def pack_state(gpu_present: bool, pack: Path | None, usable: bool = True) -> str:
    """unavailable (no NVIDIA GPU) / missing (no gpu-pack/) / old (gpu-pack/ there but this app cannot use its build,
    e.g. a stale llama build, device.pack_llama_dir) / installed."""
    if not gpu_present:
        return UNAVAILABLE
    if pack is None:
        return MISSING
    return INSTALLED if usable else OLD


def should_offer(state: str, cpu_mode: bool, dismissed: bool, offline: bool) -> bool:
    """Show the start-up banner only for an NVIDIA GPU whose pack is missing or old, when the user did not pick CPU mode,
    did not press "Don't ask again", and the app is not forced offline (TLWU_OFFLINE=1: no banner, no network)."""
    return state in (MISSING, OLD) and not cpu_mode and not dismissed and not offline


def should_autostart(state: str, cpu_mode: bool, dismissed: bool, offline: bool, auto_off: bool) -> bool:
    """#1736: start the pack download in the background without a click: same conditions as the banner, and the user
    never cancelled an automatic download before (the one-click banner stays for them)."""
    return should_offer(state, cpu_mode, dismissed, offline) and not auto_off


class AutoInstall:
    """#1736: one background GPU-pack download that the user can cancel. No tkinter: `run` is the downloader
    (gui.core.run_pack_download), `settings` the settings dict and `save` writes it. Cancel stops the child and
    remembers it in AUTO_OFF_KEY so the next start does not auto-download again."""

    def __init__(self, run, settings: dict, save):
        self._run, self.settings, self.save = run, settings, save
        self.proc = None
        self.cancelled = False

    def on_start(self, proc) -> None:  # called by the downloader right after the child started
        self.proc = proc
        if self.cancelled:
            self._stop()

    def _stop(self) -> None:
        try:
            if self.proc is not None:
                self.proc.terminate()
        except Exception:  # noqa: BLE001 - already gone
            pass

    def cancel(self) -> None:
        self.cancelled = True
        self.settings[AUTO_OFF_KEY] = True
        try:
            self.save()
        except Exception:  # noqa: BLE001 - an unwritable settings file must not break the cancel
            pass
        self._stop()

    def run_install(self, put, **kw) -> int:
        """Worker thread body (like module-level run_install, plus the cancel hook)."""
        return run_install(self._run, put, on_start=self.on_start, **kw)

    def outcome(self, rc: int, lines: list[str]) -> str:
        """"cancelled" / "ok" / "failed": never an error dialog, the GUI only picks the status line."""
        if self.cancelled:
            return "cancelled"
        return "ok" if install_ok(rc, lines) else "failed"


def offline(env=None) -> bool:
    return (os.environ if env is None else env).get("TLWU_OFFLINE") == "1"


def cpu_mode(env=None) -> bool:
    """The user picked CPU mode (`tlwu --cpu` sets CUDA_VISIBLE_DEVICES=-1, tlwu_app.main.prepare_env)."""
    return (os.environ if env is None else env).get("CUDA_VISIBLE_DEVICES", "").strip() == "-1"


def supported(platform: str | None = None) -> bool:
    """The release's GPU pack is a Windows DLL archive (#613 / #966): never offer it on Linux or macOS."""
    return (platform or sys.platform) == "win32"


def run_install(run, put, **kw) -> int:
    """Worker thread: run the `--gpu-pack` child and release INSTALLING when it really stopped (also on failure, and
    when the window that started it was closed meanwhile)."""
    try:
        return run(GPU_ROW_NAME, put, **kw)
    finally:
        INSTALLING.clear()


GPU_ROW_NAME = "gpu-pack"


_LAST_VULKAN: list[str] = []  # devices of the last detect() that returned VULKAN (the row text names them)


def vulkan_pack_ok() -> bool:
    """The installed Vulkan llama.cpp pack matches this app (mt_backend.vulkan_pack_dir, same rule as the runtime)."""
    try:
        from ..mt_backend import vulkan_pack_dir

        return vulkan_pack_dir(warn=lambda *_: None) is not None
    except Exception:  # noqa: BLE001 - unknown = not ready
        return False


def vulkan_devices(run=None, which=None) -> list[str]:
    """#1772: names of the Vulkan GPUs (`vulkaninfo --summary`, software rasterizers dropped); [] when none / no tool."""
    import shutil
    import subprocess

    exe = (which or shutil.which)("vulkaninfo")
    if not exe:
        return []
    try:
        p = (run or subprocess.run)([exe, "--summary"], capture_output=True, text=True, timeout=20,
                                    encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - broken driver = no device
        return []
    out: list[str] = []
    for name in re.findall(r"^\s*deviceName\s*=\s*(.+?)\s*$", p.stdout or "", re.M):
        if not any(w in name.lower() for w in ("llvmpipe", "swiftshader", "lavapipe", "software")) and name not in out:
            out.append(name)
    return out


def detect(gpu_info=None, pack_dir=None, usable=None, platform: str | None = None, vulkan=None) -> str:
    """pack_state for this machine, or VULKAN (#1772) when there is no NVIDIA GPU but a Vulkan one (`vulkan` = device
    list provider). nvidia-smi (hwprobe.gpu_info) decides "NVIDIA GPU present", not CTranslate2: the whole point is
    the case where CUDA does not load because the pack is missing (#1525 field test)."""
    state = _detect_nvidia(gpu_info, pack_dir, usable, platform)
    if state == UNAVAILABLE and (platform or sys.platform) != "darwin":
        devs = (vulkan or vulkan_devices)()
        if devs:
            _LAST_VULKAN[:] = devs
            return VULKAN
    return state


def _detect_nvidia(gpu_info=None, pack_dir=None, usable=None, platform: str | None = None) -> str:
    if not supported(platform):
        return UNAVAILABLE
    if gpu_info is None:
        from ..hwprobe import gpu_info
    if pack_dir is None:
        from ..gpu_pack import pack_dir
    name, _ = gpu_info()
    pack = pack_dir()
    if usable is None:
        usable = lambda p: _pack_usable(p, platform)  # noqa: E731 - same platform as the support check
    return pack_state(name is not None, pack, usable(pack) if pack is not None else True)


def _pack_usable(pack: Path, platform: str | None = None) -> bool:
    """False when the pack lacks a CUDA runtime library the app needs (device._LIBS: cuBLAS + cuDNN, Windows pack) or
    ships a CUDA llama build that this app refuses (llama-cpp-python version mismatch, #881)."""
    if (platform or sys.platform) == "win32":
        from ..device import _LIBS

        if not all((Path(pack) / n).is_file() for n in _LIBS["win32"]):
            return False
    if not (Path(pack) / "llama.dll").is_file():
        return True
    if not all((Path(pack) / n).is_file() for n in ("ggml.dll", "ggml-cuda.dll")):  # = device.pack_llama_dir
        return False
    try:
        from ..device import PACK_LLAMA_VERSION_FILE
        from ..llamacheck import LLAMA_VERSION

        have = (Path(pack) / PACK_LLAMA_VERSION_FILE).read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001 - unreadable marker = the app ignores that build
        return False
    return have == LLAMA_VERSION


def gpu_pack_cmd(mdir=None, bundled: bool | None = None) -> list[str]:
    """`tlwu.exe --run-setup --gpu-pack --models-dir ...` (bundled) or `python -m tinglongwu_setup --gpu-pack ...`:
    the existing #966 worker (it adds --app-version itself when bundled)."""
    from .core import download_cmd

    cmd = download_cmd("small", mdir, bundled)
    i = cmd.index("--whisper")
    return [*cmd[:i], "--gpu-pack", *cmd[i + 2:]]


def install_ok(rc: int, lines: list[str]) -> bool:
    """The worker exits 0 also when the release has no GPU pack (one note, #966): that is not "installed"."""
    return rc == 0 and not any(ln.startswith(NO_PACK_PREFIX) for ln in lines)


def fail_reason(lines: list[str]) -> str:
    """One reason line from the worker output: its last `gpu pack: FAIL ...` line, else its last line."""
    for ln in reversed(lines):
        if ln.startswith(NO_PACK_PREFIX):
            return "this release has no GPU pack"
        if "FAIL" in ln:
            return ln.split("FAIL", 1)[1].strip(" :")[:160]
    return (lines[-1] if lines else "").strip()[:160]


def vulkan_text(devices: list[str], pack_ok: bool | None = None) -> str:
    """#1772: GUI status line for a Vulkan GPU: names the device and the Vulkan pack state (None = unknown)."""
    from . import core

    name = ", ".join(devices) if devices else "?"
    key = "gpu_vulkan_unknown" if pack_ok is None else ("gpu_vulkan_ready" if pack_ok else "gpu_vulkan_nopack")
    return core.ui_text(key, name=name)  # #1808: plain wording unless "Show technical details" is on


def row_text(state: str, version: str | None, devices: list[str] | None = None, pack_ok=None) -> str:
    """Status text of the GPU row in Settings > Add-ons (localized)."""
    from ..i18n import t
    from . import core

    if state == VULKAN:
        return vulkan_text(devices or list(_LAST_VULKAN), (pack_ok or vulkan_pack_ok)())

    if state == INSTALLED:
        return t("gpu_pack_row_installed", ver=f" v{version}" if version else "")
    if state == UNAVAILABLE:
        return core.ui_text("gpu_pack_row_unavailable")
    if state == OLD:
        return t("gpu_pack_row_old", size=GPU_PACK_SIZE)
    return t("gpu_pack_row_missing", size=GPU_PACK_SIZE)


def is_missing_pack_notice(text: str) -> bool:
    """The worker's "GPU pack not installed ({libs} missing) ..." notice (device.gpu_pack_message), in any UI language."""
    from ..i18n import MESSAGES

    if not text:
        return False
    for table in MESSAGES.values():  # every language: a notice queued before a UI-language switch still matches
        head = table.get("gpu_pack_missing", "").split("{libs}")[0].strip().removeprefix("tlwu: ")
        if head and head in text:
            return True
    return False
