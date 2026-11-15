"""#966: `tlwu-setup --gpu-pack` installs the release's GPU pack (cuBLAS / cuDNN DLLs) into `gpu-pack/` next to the app.

The release attaches `tinglongwu-gpu-pack.zip` and `tinglongwu-gpu-pack.zip.manifest.json` ({size, sha256, ...},
tinglongwu/gpu_pack.py, #613). The pack of the installed app's own release is downloaded (resumable), checked against
the manifest's size and SHA256 before anything is unpacked, and unpacked where `tinglongwu.gpu_pack.pack_dir()` looks
(TLWU_GPU_PACK moves it). Setup must not import the runtime package, so this is a second small copy of that installer.
A release without the pack is not an error: one note, exit 0, the CPU path stays in use.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.error
import zipfile
from pathlib import Path

from . import resume, selfupdate

PACK_DIR_NAME = "gpu-pack"  # == tinglongwu.gpu_pack.PACK_NAME
ASSET = "tinglongwu-gpu-pack.zip"
MANIFEST_ASSET = ASSET + ".manifest.json"
NO_PACK_NOTE = "gpu pack: this release has no GPU pack (tinglongwu-gpu-pack.zip); skipped, the CPU path stays in use."


class PackError(Exception):
    """The downloaded archive is not the pack its manifest describes."""


def default_dest(root: Path, env=None) -> Path:
    env = os.environ if env is None else env
    return Path(env["TLWU_GPU_PACK"]) if env.get("TLWU_GPU_PACK") else Path(root) / PACK_DIR_NAME


def release_assets(version: str, get=selfupdate._get) -> dict | None:
    """{'version', 'zip_url', 'manifest_url'} of release v<version> (None for a missing / pre-release / draft tag;
    a URL is None when the release lacks that asset). Other network errors propagate."""
    try:
        rel = json.loads(get(selfupdate.API_TAG.format(version=version)).decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    tag = rel.get("tag_name") or ""
    if rel.get("prerelease") or rel.get("draft") or selfupdate.parse_version(tag) is None:
        return None
    assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets") or []}
    return {"version": tag.lstrip("v"), "zip_url": assets.get(ASSET), "manifest_url": assets.get(MANIFEST_ASSET)}


def install_zip(archive: Path, man: dict, dest: Path) -> Path:
    """Verify size + SHA256 against `man`, unpack into `dest` (replacing an older pack). A bad archive leaves `dest` alone."""
    if archive.stat().st_size != man["size"]:
        raise PackError(f"{archive.name}: size {archive.stat().st_size} != manifest {man['size']}")
    got = resume._sha(archive)
    if got != str(man["sha256"]).lower():
        raise PackError(f"{archive.name}: sha256 {got} != manifest {man['sha256']}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".gpu-pack-", dir=dest.parent))
    try:
        with zipfile.ZipFile(archive) as z:
            root = tmp.resolve()
            for n in z.namelist():  # no path escape (zip-slip)
                if not (root / n).resolve().is_relative_to(root):
                    raise PackError(f"unsafe path in pack: {n}")
            z.extractall(tmp)
        backup = None
        if dest.exists():  # keep the old pack until the new one is in place
            backup = Path(tempfile.mkdtemp(prefix=".gpu-pack-old-", dir=dest.parent)) / "pack"
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


def run(dest: Path, version: str | None, get=selfupdate._get, fetch=resume.fetch, out=print, env=None) -> int:
    """0 = installed, or that release has no GPU pack (one note); 1 = failed (offline, unknown version, network, checksum)."""
    env = os.environ if env is None else env
    if env.get("TLWU_OFFLINE") == "1":
        out("gpu pack: FAIL offline (TLWU_OFFLINE=1)")
        return 1
    if not version or selfupdate.parse_version(version) is None:
        out(f"gpu pack: FAIL the installed app version is unknown (got {version!r})")
        return 1
    try:
        rel = release_assets(version, get)
        if rel is None or not rel["zip_url"]:
            out(NO_PACK_NOTE)
            return 0
        if not rel["manifest_url"]:
            raise ValueError(f"{MANIFEST_ASSET} is missing from the release: refusing an unchecked download")
        man = json.loads(get(rel["manifest_url"], timeout=selfupdate.SUMS_TIMEOUT_S).decode("utf-8"))
        if not isinstance(man, dict) or not isinstance(man.get("size"), int) or not man.get("sha256"):
            raise ValueError(f"{MANIFEST_ASSET} needs size and sha256")
        out(f"gpu pack {rel['version']}: downloading {ASSET}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=".gpu-pack-dl-", dir=dest.parent))
        try:
            install_zip(fetch(rel["zip_url"], work / ASSET, str(man["sha256"]).lower()), man, dest)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        out(f"gpu pack {rel['version']}: installed in {dest}")
        return 0
    except (ValueError, PackError, OSError) as e:
        out(f"gpu pack: FAIL {type(e).__name__}: {e}"[:300])
        return 1
