"""Optional packs (#615, part 5 of #23): speak, meeting-notes and per-language packs described by one manifest.

Manifest = {"packs": {name: {"feature", "size", "sha256", "url"}}}. The installed state is a small JSON file
({name: sha256}) next to the models. `plan_upgrade` decides what an upgrade of the core downloads: never a pack the
user already has (even if the manifest lists a newer build of it; that is only reported as `outdated`).
`feature_status` words a plain message when the pack behind a feature was removed; nothing else is disabled.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PACK_KEYS = ("feature", "size", "sha256", "url")
FEATURES = {"speak": "speak mode (voices and virtual microphone)", "meeting-notes": "meeting notes"}
SHA_RE = re.compile(r"[0-9a-fA-F]{64}")


class ManifestError(Exception):
    """The pack manifest is malformed."""


def feature_of(name: str) -> str:
    """Each pack is its own feature, so a removed ko pack never disables an installed ja pack."""
    return name


def make_manifest(entries: dict[str, dict]) -> dict:
    """entries = {pack name: {"size": bytes, "sha256": hex, "url": str}}; the feature is derived from the name."""
    packs = {}
    for name, rec in sorted(entries.items()):
        packs[name] = {"feature": feature_of(name), "size": rec["size"], "sha256": rec["sha256"], "url": rec["url"]}
    return validate({"packs": packs})


def validate(man) -> dict:
    packs = man.get("packs") if isinstance(man, dict) else None
    if not isinstance(packs, dict) or not packs:
        raise ManifestError("manifest needs a non-empty 'packs' object")
    for name, rec in packs.items():
        if not isinstance(rec, dict) or any(k not in rec for k in PACK_KEYS):
            raise ManifestError(f"pack {name}: needs {', '.join(PACK_KEYS)}")
        if type(rec["size"]) is not int or rec["size"] <= 0:  # bool is an int subclass
            raise ManifestError(f"pack {name}: size must be a positive number of bytes")
        if not isinstance(rec["sha256"], str) or not SHA_RE.fullmatch(rec["sha256"]):
            raise ManifestError(f"pack {name}: sha256 must be 64 hex characters")
    return man


def load_manifest(path: Path) -> dict:
    try:
        return validate(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError) as e:
        raise ManifestError(f"manifest unreadable: {e}") from e


def load_installed(path: Path) -> dict[str, str]:
    """{pack name: sha256 installed}; a missing or broken file means nothing is installed."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def plan_upgrade(man: dict, installed: dict[str, str], wanted: list[str] | None = None) -> dict[str, list[str]]:
    """What the upgrade fetches: only packs in `wanted` (default none) that are not installed. Installed packs are
    kept; an installed pack whose sha256 differs from the manifest is listed in `outdated` but not downloaded."""
    packs = man["packs"]
    keep = sorted(installed)  # also packs a newer manifest no longer lists
    outdated = [n for n in keep if n in packs and installed[n].lower() != packs[n]["sha256"].lower()]
    download = [n for n in (wanted or []) if n in packs and n not in installed]
    return {"download": download, "keep": keep, "outdated": outdated}


def feature_status(feature: str, man: dict, installed: dict[str, str]) -> tuple[bool, str]:
    """(available, plain message). A feature is available when every pack that provides it is installed."""
    need = sorted(n for n, r in man["packs"].items() if r["feature"] == feature)
    gone = [n for n in need if n not in installed]
    if need and not gone:
        return True, ""
    label = FEATURES.get(feature, feature)
    if not need:
        return False, f"{label} is not part of this release."
    return False, f"{label} is off because its pack ({', '.join(gone)}) is not installed. Everything else still works."


def sizes_md(man: dict) -> str:
    """Release-notes section: one line per pack with its size in MB and the first 12 hex digits of its sha256."""
    lines = ["### Optional packs (downloaded on demand)", "", "| pack | size | sha256 |", "|---|---|---|"]
    for name, r in sorted(man["packs"].items()):
        lines.append(f"| {name} | {r['size'] / 1e6:.1f} MB | `{str(r['sha256'])[:12]}` |")
    return "\n".join(lines)
