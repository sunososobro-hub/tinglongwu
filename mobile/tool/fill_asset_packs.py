#!/usr/bin/env python3
"""Copies the checked model files into the Play Asset Delivery packs for the AAB.

    python3 -I tool/fill_asset_packs.py <models_dir>

<models_dir> is the `models` folder from tool/fetch_models.sh. Every bundle file in
assets/models_manifest.json (entries without their own `url`) must be there with the manifest size and SHA-256, or the
build fails. Files keep their manifest path inside the pack (the app looks for
<pack>/<path>). Hard links are used when possible (the models are > 1 GB).
"""
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MOBILE = os.path.dirname(HERE)
MANIFEST = os.path.join(MOBILE, "assets", "models_manifest.json")
# manifest group -> asset pack (keep each pack under Play's 1.5 GB on-demand limit)
PACK_OF_GROUP = {"asr": "models_asr", "vad": "models_asr", "llm": "models_llm"}
PACK_LIMIT = 1536 << 20


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    src_root = sys.argv[1]
    with open(MANIFEST, encoding="utf-8") as f:
        files = json.load(f)["files"]
    sizes = {}
    for e in files:
        if e.get("url"):
            continue  # optional file with its own official link, not in the bundle
        pack = PACK_OF_GROUP.get(e.get("group"))
        if pack is None:
            sys.exit(f"{e['path']}: group {e.get('group')!r} has no asset pack")
        src = os.path.join(src_root, e["path"])
        if os.path.getsize(src) != e["size"] or sha256(src) != e["sha256"]:
            sys.exit(f"{e['path']}: size or SHA-256 does not match the manifest")
        dst = os.path.join(MOBILE, "android", pack, "src", "main", "assets", e["path"])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            os.remove(dst)
        try:
            os.link(src, dst)
        except OSError:
            shutil.copyfile(src, dst)
        sizes[pack] = sizes.get(pack, 0) + e["size"]
        print(f"{pack}: {e['path']} ({e['size']} bytes)")
    for pack, n in sizes.items():
        if n > PACK_LIMIT:
            sys.exit(f"{pack}: {n} bytes is over the 1.5 GB on-demand pack limit")


if __name__ == "__main__":
    main()
