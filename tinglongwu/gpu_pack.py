"""GPU pack (#613, part 3 of #23): the CUDA DLLs as a separate zip described by a manifest.

Manifest = {"name", "size", "sha256", "url"}. `make_manifest` writes it at build time, `install_pack` verifies size and
SHA256 before anything is extracted and rejects a mismatch, `pack_dir` is the folder the app adds to the DLL search
path. No pack dir -> `pack_dir()` is None and the app stays on the CPU path (nothing raises).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

PACK_NAME = "gpu-pack"
MANIFEST_KEYS = ("name", "size", "sha256", "url")  # old manifests have no "wheels_key" (#763, optional) and still load


class PackError(Exception):
    """The pack archive does not match its manifest (or the manifest is malformed)."""


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_manifest(archive: Path, url: str, name: str = PACK_NAME) -> dict:
    return {"name": name, "size": archive.stat().st_size, "sha256": sha256_file(archive), "url": url}


def write_manifest(man: dict, path: Path) -> None:
    path.write_text(json.dumps(man, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_manifest(path: Path) -> dict:
    try:
        man = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PackError(f"manifest unreadable: {e}") from e
    if not isinstance(man, dict) or any(k not in man for k in MANIFEST_KEYS):
        raise PackError(f"manifest needs {', '.join(MANIFEST_KEYS)}")
    return man


def verify(archive: Path, man: dict) -> None:
    if archive.stat().st_size != man["size"]:
        raise PackError(f"{archive.name}: size {archive.stat().st_size} != manifest {man['size']}")
    got = sha256_file(archive)
    if got != str(man["sha256"]).lower():
        raise PackError(f"{archive.name}: sha256 {got} != manifest {man['sha256']}")


def install_pack(archive: Path, man: dict, dest: Path) -> Path:
    """Verify, then unpack into dest (replacing an older pack). A bad archive leaves dest untouched."""
    verify(archive, man)
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
            backup = Path(tempfile.mkdtemp(prefix=".gpu-pack-old-", dir=dest.parent)) / "pack"  # unique, ours alone
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


def pack_dir(base: Path | None = None) -> Path | None:
    """The installed GPU pack folder (has at least one DLL / .so), else None. Env TLWU_GPU_PACK overrides the place."""
    d = base or (Path(os.environ["TLWU_GPU_PACK"]) if os.environ.get("TLWU_GPU_PACK")
                 else Path(__file__).resolve().parent.parent / PACK_NAME)
    try:
        if d.is_dir() and (any(d.glob("*.dll")) or any(d.glob("*.so*"))):
            return d.resolve()
    except OSError:
        pass
    return None
