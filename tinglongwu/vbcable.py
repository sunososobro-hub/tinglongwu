"""#804 (part 1 of #758): one-click VB-CABLE install for speak mode on Windows. No network and no tkinter here.

Speak mode plays the translated voice into "CABLE Input"; Meet / Teams / Zoom record its "CABLE Output" end. When no
CABLE device exists, the GUI asks once ("需要安裝虛擬麥克風（免費，約 1 MB）[安裝]"). On yes the official VB-Audio
package is downloaded at click time (never bundled) by a `python -m tinglongwu_setup.vbcable` child (the network
guard in tinglongwu.offline is never relaxed in this process), its SHA-256 is checked against VBCABLE_SHA256 (here,
again, before anything from it runs), and its setup is started elevated (UAC prompt). Then the 3-step "Meet / Teams /
Zoom -> microphone -> CABLE Output" help is shown.

- No pin yet (VBCABLE_SHA256 == ""): nothing is downloaded; the official page VBCABLE_PAGE opens instead. The pin is
  measured on a Windows PC (part 2; vb-audio.com is blocked from the cloud workers).
- TLWU_OFFLINE=1: nothing is downloaded; the page opens (the user can install by hand).
- Cancel / failure / CABLE still missing: the GUI stays in (switches back to) listen mode.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from .reverse.vmic import pick_win_cable

VBCABLE_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"
VBCABLE_SHA256 = ""  # pinned by part 2 (measured on the home PC); empty -> open VBCABLE_PAGE instead of downloading
VBCABLE_PAGE = "https://vb-audio.com/Cable/"
SIZE_MB = 1
MODULE = "tinglongwu_setup.vbcable"

TEXT = {
    "zh-TW": "需要安裝虛擬麥克風（免費，約 1 MB）[安裝]",
    "en": "Speak mode needs a virtual microphone (VB-CABLE, free, about 1 MB). [Install]",
    "ru": "Для режима речи нужен виртуальный микрофон (VB-CABLE, бесплатно, около 1 МБ). [Установить]",
}
TITLE = {"zh-TW": "虛擬麥克風", "en": "Virtual microphone", "ru": "Виртуальный микрофон"}
FAIL = {
    "zh-TW": "虛擬麥克風沒有裝好，先回到聽模式。可到 https://vb-audio.com/Cable/ 手動安裝（裝完可能要重開機）。",
    "en": "The virtual microphone was not installed; back to listen mode. Install it from https://vb-audio.com/Cable/ "
          "(a restart may be needed).",
    "ru": "Виртуальный микрофон не установлен; снова режим прослушивания. Установите его с https://vb-audio.com/Cable/ "
          "(может понадобиться перезагрузка).",
}
HOWTO = {
    "zh-TW": "虛擬麥克風已安裝。\n1. 開啟 Meet / Teams / Zoom 的設定\n2. 麥克風\n3. 選「CABLE Output」",
    "en": "Virtual microphone installed.\n1. Open Meet / Teams / Zoom settings\n2. Microphone\n"
          "3. Choose \"CABLE Output\"",
    "ru": "Виртуальный микрофон установлен.\n1. Откройте настройки Meet / Teams / Zoom\n2. Микрофон\n"
          "3. Выберите \"CABLE Output\"",
}


def _pick(table: dict, ui_lang: str) -> str:
    return table.get(ui_lang) or table.get((ui_lang or "").split("-")[0]) or table["en"]


def dialog_text(ui_lang: str) -> str:
    return _pick(TEXT, ui_lang)


def howto_text(ui_lang: str) -> str:
    return _pick(HOWTO, ui_lang)


def title_text(ui_lang: str) -> str:
    return _pick(TITLE, ui_lang)


def fail_text(ui_lang: str) -> str:
    return _pick(FAIL, ui_lang)


def output_names() -> list[str]:
    """All output device names (soundcard); [] when there is no audio stack (then nothing is offered)."""
    try:
        import soundcard as sc

        return [sp.name for sp in sc.all_speakers()]
    except Exception:  # noqa: BLE001
        return []


def has_cable(names) -> bool:
    """True when an output device named like "CABLE Input" exists (the speak side plays into it)."""
    return pick_win_cable([n for n in (names or []) if n]) is not None


def offline(env=None) -> bool:
    return (os.environ if env is None else env).get("TLWU_OFFLINE") == "1"


def needs_offer(names, platform: str | None = None) -> bool:
    """Ask before a speak Start: Windows only, and only when the device list is known (non-empty) and has no CABLE."""
    return (platform or sys.platform) == "win32" and bool(names) and not has_cable(names)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def verified(path, pin: str | None = None) -> bool:
    """The downloaded file exists and its SHA-256 equals the pin (an empty pin never verifies)."""
    pin = VBCABLE_SHA256 if pin is None else pin
    try:
        return bool(pin) and path is not None and sha256_of(Path(path)) == pin.lower()
    except OSError:
        return False


def install_flow(download, install, pin: str | None = None, env=None) -> str:
    """Worker-thread part (after the user said yes). Returns "installed" | "page" (no pin / offline: the caller opens
    VBCABLE_PAGE) | "bad-sha" | "fail". `download() -> path or None`; `install(path) -> returncode`. `install` runs
    only on a file whose SHA-256 matches the pin."""
    pin = VBCABLE_SHA256 if pin is None else pin
    if not pin or offline(env):
        return "page"
    try:
        path = download()
    except Exception:  # noqa: BLE001 - a failed download leaves listen mode on
        return "fail"
    if path is None:
        return "fail"
    if not verified(path, pin):
        try:
            Path(path).unlink()
        except OSError:
            pass
        return "bad-sha"
    try:
        return "installed" if install(path) == 0 else "fail"
    except Exception:  # noqa: BLE001
        return "fail"


def ensure_cable(names, ask, download, install, open_url, list_names=None, ui_lang: str = "en", pin: str | None = None,
                 env=None, platform: str | None = None) -> str:
    """The whole flow, synchronous (the GUI runs `install_flow` on a thread; tests and the CLI call this). Returns the
    mode to be in afterwards: "speak" when a CABLE device is there (already, or after the install), else "listen".
    `ask(text) -> bool`; `open_url(url)`; `list_names() -> names` re-reads the devices after the install."""
    if not needs_offer(names, platform):
        return "speak"
    if not ask(dialog_text(ui_lang)):
        return "listen"
    res = install_flow(download, install, pin, env)
    if res == "page":
        open_url(VBCABLE_PAGE)
        return "listen"
    if res == "installed" and has_cable(list_names() if list_names else []):
        return "speak"
    return "listen"


# -- the child process (networked code lives in tinglongwu_setup) ------------------------------------------------
def download_dir() -> Path:
    import tempfile

    return Path(tempfile.gettempdir()) / "tinglongwu-vbcable"


def _child_env(env=None) -> dict:
    from .offline import OFFLINE_ENV

    out = {k: v for k, v in (os.environ if env is None else env).items() if k not in OFFLINE_ENV}
    out.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    return out


def download_cmd(dest: Path, python: str | None = None, pin: str | None = None) -> list[str]:
    return [python or sys.executable, "-m", MODULE, "--download", str(dest), "--url", VBCABLE_URL,
            "--sha256", VBCABLE_SHA256 if pin is None else pin]


def install_cmd(zip_path, python: str | None = None, pin: str | None = None) -> list[str]:
    return [python or sys.executable, "-m", MODULE, "--install", str(zip_path),
            "--sha256", VBCABLE_SHA256 if pin is None else pin]


def _run(cmd, run=None) -> subprocess.CompletedProcess:
    # the child prints UTF-8 (PYTHONIOENCODING, reconfigure); decode it as UTF-8, not the Windows ANSI code page (#804)
    return (run or subprocess.run)(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   env=_child_env(), timeout=600,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def child_download(dest: Path | None = None, run=None) -> str | None:
    """Run the download child; the path from its "READY <path>" line, or None."""
    r = _run(download_cmd(dest or download_dir()), run)
    for line in (r.stdout or "").splitlines():
        if line.startswith("READY "):
            return line[6:].strip()
    return None


def child_install(zip_path, run=None) -> int:
    """Run the install child (extract + elevated setup, UAC prompt). Its return code."""
    return _run(install_cmd(zip_path), run).returncode
