"""#801 (part 2 of #797): `tlwu-setup --vulkan-pack` installs the release's Vulkan llama.cpp pack into `vulkan-pack/`.

The release attaches `tlwu-vulkan-pack-<version>.zip` (part 1) and lists it in SHA256SUMS.txt. This downloads it
(resumable, SHA256-checked against that list, like the wizard self-update), unpacks it next to the app, and
`tinglongwu.mt_backend.vulkan_pack_dir()` then finds it (it holds `llama.dll` / `ggml-vulkan.dll`).

Same rules as the GPU pack installer (tinglongwu/gpu_pack.py, #613): verify before anything is unpacked, refuse paths
that escape the folder, keep the old pack until the new one is in place, leave nothing behind on a failure. It is a
second small copy because setup must not import the runtime package (tests/test_no_network_code.py).
A release without the zip is not an error: one note, exit 0, the CPU build stays in use.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from . import resume, selfupdate

PACK_DIR_NAME = "vulkan-pack"  # == tinglongwu.mt_backend.VULKAN_PACK_NAME (setup does not import tinglongwu)
ASSET_FMT = "tlwu-vulkan-pack-{version}.zip"
NO_PACK_NOTE = ("vulkan pack: this release has no Vulkan pack (tlwu-vulkan-pack-<version>.zip); skipped, "
                "MT keeps running on the CPU build.")


class PackError(Exception):
    """The downloaded archive is not a usable Vulkan pack."""


def default_dest(root: Path, env=None) -> Path:
    """`vulkan-pack/` next to the app; TLWU_VULKAN_PACK moves it (same override as mt_backend.vulkan_pack_dir)."""
    env = os.environ if env is None else env
    return Path(env["TLWU_VULKAN_PACK"]) if env.get("TLWU_VULKAN_PACK") else Path(root) / PACK_DIR_NAME


def release_assets(get=selfupdate._get) -> dict | None:
    """{'version', 'name', 'zip_url', 'sums_url'} of the latest published release (zip_url / sums_url None when the
    release has no such asset), or None for a pre-release / draft / unparseable tag. Network errors propagate."""
    rel = selfupdate._latest_json(get, None)
    if rel.get("prerelease") or rel.get("draft"):
        return None
    tag = rel.get("tag_name") or ""
    if selfupdate.parse_version(tag) is None:
        return None
    version = tag.lstrip("v")
    name = ASSET_FMT.format(version=version)
    assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets") or []}
    return {"version": version, "name": name, "zip_url": assets.get(name), "sums_url": assets.get(selfupdate.SUMS_NAME)}


def _has_llama_library(folder: Path) -> bool:
    # the rule of mt_backend.vulkan_pack_dir: a pack is a folder with a llama library at its top
    return any(folder.glob("llama.*")) or any(folder.glob("libllama.*"))


def install_zip(archive: Path, sha256: str, dest: Path) -> Path:
    """Verify, unpack into `dest` (replacing an older pack). A bad archive leaves `dest` and its neighbours untouched."""
    got = resume._sha(archive)
    if got != sha256.lower():
        raise PackError(f"{archive.name}: sha256 {got} != expected {sha256.lower()}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".vulkan-pack-", dir=dest.parent))
    try:
        with zipfile.ZipFile(archive) as z:
            root = tmp.resolve()
            for n in z.namelist():  # no path escape (zip-slip)
                if not (root / n).resolve().is_relative_to(root):
                    raise PackError(f"unsafe path in pack: {n}")
            z.extractall(tmp)
        if not _has_llama_library(tmp):  # never replace a working pack with something the app would not accept
            raise PackError("not a Vulkan llama.cpp pack: no llama library at the top of the zip")
        backup = None
        if dest.exists():  # keep the old pack until the new one is in place
            backup = Path(tempfile.mkdtemp(prefix=".vulkan-pack-old-", dir=dest.parent)) / "pack"
            dest.rename(backup)
        try:
            tmp.rename(dest)
        except OSError:
            if backup is not None:
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                backup.rename(dest)
                shutil.rmtree(backup.parent, ignore_errors=True)
            raise
        if backup is not None:
            shutil.rmtree(backup.parent, ignore_errors=True)
    except zipfile.BadZipFile as e:
        raise PackError(f"not a zip: {e}") from e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dest


def run(dest: Path, get=selfupdate._get, fetch=resume.fetch, out=print) -> int:
    """0 = installed, or the release has no Vulkan pack (one note); 1 = failed (network, checksum, bad archive)."""
    try:
        rel = release_assets(get)
        if rel is None or not rel["zip_url"]:
            out(NO_PACK_NOTE)
            return 0
        if not rel["sums_url"]:
            raise ValueError(f"{selfupdate.SUMS_NAME} is missing from the release: refusing an unchecked download")
        sha = selfupdate.parse_sums(get(rel["sums_url"], timeout=selfupdate.SUMS_TIMEOUT_S).decode("utf-8", "replace"),
                                    rel["name"])
        if not sha:
            raise ValueError(f"{rel['name']} is not listed in {selfupdate.SUMS_NAME}")
        out(f"vulkan pack {rel['version']}: downloading {rel['name']}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=".vulkan-pack-dl-", dir=dest.parent))
        try:
            install_zip(fetch(rel["zip_url"], work / rel["name"], sha), sha, dest)
        finally:
            shutil.rmtree(work, ignore_errors=True)  # also removes a .part left by a failed download
        out(f"vulkan pack {rel['version']}: installed in {dest}")
        return 0
    except (ValueError, PackError, OSError) as e:  # URLError / timeouts are OSError
        out(f"vulkan pack: FAIL {type(e).__name__}: {e}"[:300])
        return 1
