"""Build packaging/packs.json (#722): the #615 optional-pack manifest the release notes read (release.yml passes it to
`scripts/release_notes.py notes --packs-manifest`). Offline: every number comes from a committed, measured pin.

  python scripts/make_pack_manifest.py            rewrite packaging/packs.json
  python scripts/make_pack_manifest.py --check    exit 1 when packaging/packs.json differs from what the pins give

Sources (no estimates):
  - Hy-MT2-1.8B GGUF (the ja / ko packs, tinglongwu_setup.packs.PACKS): SHA-256 + HF revision from
    tinglongwu_setup/manifest.json, byte size from mobile/assets/models_manifest.json (the same file, its SHA-256 must
    match, else this script stops).
  - Kokoro (the speak pack, en/ja voice): bytes + SHA-256 of both files from tinglongwu_setup.downloader.KOKORO_FILES
    (measured in #124).
A pack made of several files gets size = sum of the files and sha256 = SHA-256 of its sorted "<sha256>  <name>\\n"
lines (a content id: it changes when any file changes, which is what pack_manifest.plan_upgrade compares).
  - Piper ru voice dmitri (the ru pack = Hy-MT2 + this voice): both file SHA-256s + HF revision from
    tinglongwu_setup/manifest.json, bytes from downloader.PIPER_RU_DMITRI_SIZES (measured in #818).
Not listed until pinned: meeting-notes (no separate download yet).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packaging" / "packs.json"
sys.path.insert(0, str(ROOT))

from tinglongwu_setup import downloader, packs  # noqa: E402  (setup package: no network guard, no network here)


def _pack_manifest():  # by path: importing the tinglongwu package would install its offline guard in this script
    spec = importlib.util.spec_from_file_location("tlwu_pack_manifest", ROOT / "tinglongwu" / "pack_manifest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def content_id(files: dict[str, str]) -> str:
    """SHA-256 of the sorted "<sha256>  <name>" lines of a multi-file pack."""
    return hashlib.sha256("".join(f"{h.lower()}  {n}\n" for n, h in sorted(files.items())).encode()).hexdigest()


def hymt_entry(root: Path = ROOT) -> dict:
    pin = json.loads((root / "tinglongwu_setup" / "manifest.json").read_text("utf-8"))["models"][downloader.HYMT_DIR]
    repo, name = downloader.HYMT_GGUF
    sha = pin["files"][name]
    mobile = json.loads((root / "mobile" / "assets" / "models_manifest.json").read_text("utf-8"))
    rec = next((f for f in mobile["files"] if f["path"] == name), None)
    if rec is None or rec["sha256"].lower() != sha.lower():
        raise SystemExit(f"{name}: no measured size with the pinned SHA-256 in mobile/assets/models_manifest.json")
    return {"size": int(rec["size"]), "sha256": sha,
            "url": f"https://huggingface.co/{repo}/resolve/{pin['revision']}/{name}"}


def kokoro_entry() -> dict:
    files = downloader.KOKORO_FILES
    return {"size": sum(b for b, _ in files.values()), "sha256": content_id({n: h for n, (_, h) in files.items()}),
            "url": downloader.KOKORO_BASE + "/"}


def ru_entry(root: Path = ROOT) -> dict:
    """ru pack = Hy-MT2 GGUF + both Piper dmitri files: size = sum of bytes, sha256 = content id of the 3 files."""
    man = json.loads((root / "tinglongwu_setup" / "manifest.json").read_text("utf-8"))["models"]
    hymt, voice = hymt_entry(root), man[packs.PIPER_RU_DEFAULT]
    files = {downloader.HYMT_GGUF[1]: hymt["sha256"], **voice["files"]}
    size = hymt["size"] + sum(downloader.PIPER_RU_DMITRI_SIZES[n] for n in voice["files"])
    return {"size": size, "sha256": content_id(files), "url": hymt["url"]}


def build(root: Path = ROOT) -> dict:
    entries = {}
    hymt = hymt_entry(root)
    for lang, keys in packs.PACKS.items():
        if keys == [downloader.HYMT_DIR]:  # ja, ko: the pack is exactly the Hy-MT2 GGUF
            entries[lang] = dict(hymt)
    entries["ru"] = ru_entry(root)
    entries["speak"] = kokoro_entry()
    return _pack_manifest().make_manifest(entries)


def render(man: dict) -> str:
    return json.dumps(man, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python scripts/make_pack_manifest.py")
    p.add_argument("--check", action="store_true")
    p.add_argument("--out", default=str(OUT))
    a = p.parse_args(argv)
    text, out = render(build()), Path(a.out)
    if a.check:
        if not out.exists() or out.read_text("utf-8") != text:
            print(f"{out} is stale: run python scripts/make_pack_manifest.py")
            return 1
        print(f"{out} up to date")
        return 0
    out.write_text(text, "utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
