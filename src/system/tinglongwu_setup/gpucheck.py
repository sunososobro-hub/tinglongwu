"""#1772: `tlwu-setup --check-gpu`: offline report of the Vulkan path for ASR and MT (no network, no tinglongwu import).

Prints, per stage: Vulkan devices found, pack path + version vs the app, whisper.cpp model present, chosen backend and
why. `probe_devices` is the only part that touches the machine (`vulkaninfo --summary`); everything else takes plain
values so tests can feed fake devices. Software rasterizers (llvmpipe, SwiftShader, lavapipe) are not GPUs.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

PACK_DIR_NAME = "vulkan-pack"
VERSION_FILE = "version.txt"
WHISPER_SUBDIR = "whisper"
MODEL_SUBDIR = "whisper-cpp"  # models/whisper-cpp/ggml-<size>.bin
SOFTWARE = ("llvmpipe", "swiftshader", "lavapipe", "software")
_NAME_RE = re.compile(r"^\s*deviceName\s*=\s*(.+?)\s*$", re.M)


def parse_devices(text: str) -> list[str]:
    """GPU names from `vulkaninfo --summary` output; software rasterizers dropped, duplicates kept once."""
    out: list[str] = []
    for name in _NAME_RE.findall(text or ""):
        if any(s in name.lower() for s in SOFTWARE) or name in out:
            continue
        out.append(name)
    return out


def probe_devices(run=subprocess.run, which=shutil.which) -> list[str]:
    exe = which("vulkaninfo")
    if not exe:
        return []
    try:
        p = run([exe, "--summary"], capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - broken driver = no device
        return []
    return parse_devices(p.stdout)


def whisper_lib_name(platform: str) -> str:
    return "tlwu-whisper.dll" if platform == "win32" else "libtlwu-whisper.so"


def llama_lib_name(platform: str) -> str:
    return "llama.dll" if platform == "win32" else "libllama.so"


def pack_info(pack: Path, app_version: str | None, platform: str) -> dict:
    """{'state': none|stale|ok, 'version', 'whisper_lib': bool}; stale = version.txt missing or != app version."""
    pack = Path(pack)
    if not (pack.is_dir() and (pack / llama_lib_name(platform)).is_file()):
        return {"state": "none", "version": None, "whisper_lib": False}
    try:
        have = (pack / VERSION_FILE).read_text(encoding="utf-8").strip() or None
    except OSError:
        have = None
    state = "ok" if have and have == app_version else "stale"
    return {"state": state, "version": have, "whisper_lib": (pack / WHISPER_SUBDIR / whisper_lib_name(platform)).is_file()}


def models(models_dir: Path) -> list[str]:
    d = Path(models_dir) / MODEL_SUBDIR
    return sorted(p.name for p in d.glob("ggml-*.bin")) if d.is_dir() else []


def choose(devices: list[str], pack: dict, have_models: list[str]) -> dict:
    """Backend and reason per stage (mirrors the runtime: MT needs devices + a current pack; ASR also needs the pack's
    whisper.cpp library and a ggml model)."""
    if not devices:
        why_mt = why_asr = "no Vulkan device found"
    elif pack["state"] == "none":
        why_mt = why_asr = "Vulkan pack not installed: run `tlwu-setup --vulkan-pack`"
    elif pack["state"] == "stale":
        why_mt = why_asr = (f"Vulkan pack is for version {pack['version'] or 'unknown'}, not this app: "
                            "re-run `tlwu-setup --vulkan-pack`")
    else:
        why_mt = None
        if not pack["whisper_lib"]:
            why_asr = "the pack has no whisper.cpp library (whisper/): re-run `tlwu-setup --vulkan-pack`"
        elif not have_models:
            why_asr = "no whisper.cpp model (models/whisper-cpp/ggml-<size>.bin) installed"
        else:
            why_asr = None
    return {"mt": ("vulkan", "pack and device ok") if why_mt is None else ("cpu", why_mt),
            "asr": ("vulkan", "pack, device and model ok") if why_asr is None else ("cpu", why_asr)}


def report(devices: list[str], pack_path: Path, pack: dict, app_version: str | None, have_models: list[str],
           models_dir: Path) -> list[str]:
    pick = choose(devices, pack, have_models)
    lines = ["Vulkan devices: " + ("; ".join(devices) if devices else "none")]
    ptxt = {"none": "not installed", "ok": f"installed, version {pack['version']} matches the app",
            "stale": f"STALE, version {pack['version'] or 'unknown'} vs app {app_version or 'unknown'}"}[pack["state"]]
    lines.append(f"Vulkan pack: {pack_path} ({ptxt})")
    lines.append("whisper.cpp model: " + (", ".join(have_models) if have_models else f"missing (looked in {Path(models_dir) / MODEL_SUBDIR})"))
    for stage, label in (("asr", "ASR"), ("mt", "MT")):
        backend, why = pick[stage]
        lines.append(f"{label}: {backend.upper()} ({why})")
    return lines


def run(root: Path, models_dir: Path, app_version: str | None, env=None, platform: str | None = None,
        devices: list[str] | None = None, out=print) -> int:
    """Always exit 0: it is a report, not a gate."""
    import os

    env = os.environ if env is None else env
    platform = platform or sys.platform
    pack_path = Path(env["TLWU_VULKAN_PACK"]) if env.get("TLWU_VULKAN_PACK") else Path(root) / PACK_DIR_NAME
    devs = probe_devices() if devices is None else devices
    for ln in report(devs, pack_path, pack_info(pack_path, app_version, platform), app_version, models(models_dir),
                     models_dir):
        out(ln)
    return 0
