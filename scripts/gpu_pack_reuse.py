"""Reuse the previous release's GPU pack when its pinned wheels did not change (#765, part 4 of #740).

Usage: python scripts/gpu_pack_reuse.py --repo OWNER/NAME --tag vX.Y.Z --out DIR
Looks at the latest published release's tinglongwu-gpu-pack.zip.manifest.json. When its `wheels_key` equals this
checkout's (release/packaging/build_windows.gpu_pack_wheels_key), downloads the zip, checks size + sha256 against that manifest,
writes the zip and a manifest whose url names `--tag` into DIR, and prints `reused=true`. Anything else (no previous
release, no / invalid manifest, other key, bad download) prints `reused=false` and `reason=<why>`: the caller builds.
Both forms also print `summary=gpu pack: reused from vX.Y.Z` or `summary=gpu pack: built (<reason>)`.
Never raises on a network or data problem: a failed reuse is only a build.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

GPU_PACK_ASSET = "tinglongwu-gpu-pack.zip"  # = release/packaging/build_windows.py GPU_PACK_ASSET
MANIFEST_ASSET = GPU_PACK_ASSET + ".manifest.json"
GPU_PACK_REUSE_BUDGET_MIN = 3  # the reuse step (two small requests + one download + sha256) must fit in this
TIMEOUT_S = 60
HASH_CHUNK = 1 << 20


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "tlwu-release"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 (https URLs of this repo's releases)
        return r.read()


def try_reuse(repo: str, tag: str, key: str, out: Path, get=_http_get) -> tuple[bool, str]:
    """(reused, text): text is the previous tag when reused, else the reason it is built. `get(url) -> bytes`."""
    try:
        rel = json.loads(get(f"https://api.github.com/repos/{repo}/releases/latest"))
        prev = str(rel["tag_name"])
        assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    except Exception as e:  # noqa: BLE001
        return False, f"no previous release ({type(e).__name__})"
    if MANIFEST_ASSET not in assets or GPU_PACK_ASSET not in assets:
        return False, f"{prev} has no GPU pack manifest"
    try:
        man = json.loads(get(assets[MANIFEST_ASSET]))
        size, sha = man["size"], str(man["sha256"]).lower()
        if type(size) is not int or size < 0:
            raise ValueError("size is not a non-negative integer")
        prev_key = man.get("wheels_key")
    except Exception as e:  # noqa: BLE001
        return False, f"invalid manifest in {prev} ({type(e).__name__})"
    if not prev_key:
        return False, f"{prev} manifest has no wheels_key"
    if "name" not in man:
        return False, f"{prev} manifest has no name"
    if prev_key != key:
        return False, "pinned wheels changed"
    try:
        data = get(assets[GPU_PACK_ASSET])
    except Exception as e:  # noqa: BLE001
        return False, f"download of {prev} pack failed ({type(e).__name__})"
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha:
        return False, f"{prev} pack does not match its manifest"
    man["url"] = f"https://github.com/{repo}/releases/download/{tag}/{GPU_PACK_ASSET}"
    out.mkdir(parents=True, exist_ok=True)
    (out / GPU_PACK_ASSET).write_bytes(data)
    (out / MANIFEST_ASSET).write_text(json.dumps(man, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return True, prev


def main(argv: list[str] | None = None, get=_http_get) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--key", help="wheels_key; default: release/packaging/build_windows.gpu_pack_wheels_key()")
    a = ap.parse_args(argv)
    key = a.key
    if key is None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "release" / "packaging"))
        import build_windows  # noqa: PLC0415

        key = build_windows.gpu_pack_wheels_key()
    ok, text = try_reuse(a.repo, a.tag, key, a.out, get)
    print(f"reused={'true' if ok else 'false'}")
    if ok:
        print(f"summary=gpu pack: reused from {text}")
    else:
        print(f"reason={text}")
        print(f"summary=gpu pack: built ({text})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
