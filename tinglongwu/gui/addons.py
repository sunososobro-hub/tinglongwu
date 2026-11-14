"""#614: add-ons manager logic (installed/available packs, sizes, one-time-internet wording). No tkinter here:
the settings screen renders `rows()` / `offline_message()`; the manifest comes from `fetch` (injected, so no
network code lives in this runtime package)."""
from __future__ import annotations

from ..pack_manifest import ManifestError, validate

NOTE = "Needs internet to download ({size}). Works offline afterwards."
OFFLINE = "No internet connection. Retry, or pick a local models zip."
GPU_PACK = "gpu"


def size_text(nbytes: int) -> str:
    return f"{nbytes / 1e9:.1f} GB" if nbytes >= 1e9 else f"{max(1, round(nbytes / 1e6))} MB"


def rows(man: dict, installed: dict[str, str], gpu_present: bool = False) -> list[dict]:
    """One row per manifest pack: name, size text, note, installed flag, and `ticked` (pre-tick: only the GPU pack,
    only on an NVIDIA GPU, only while it is not installed). Installed packs have no download note."""
    out = []
    for name, rec in sorted(man["packs"].items()):
        have = name in installed
        out.append({"name": name, "size": size_text(rec["size"]), "installed": have,
                    "note": "" if have else NOTE.format(size=size_text(rec["size"])),
                    "ticked": gpu_present and name == GPU_PACK and not have})
    return out


def load_rows(fetch, installed: dict[str, str], gpu_present: bool = False) -> tuple[list[dict], str]:
    """(rows, message). `fetch()` returns the manifest dict; any network/format error gives ([], plain message)
    for the screen to show with Retry and "pick a local models zip" instead of a traceback."""
    try:
        return rows(validate(fetch()), installed, gpu_present), ""
    except (OSError, ManifestError, ValueError, KeyError, TypeError):
        return [], OFFLINE
