#!/usr/bin/env python3
"""Dev tool (NOT part of the offline runtime): download FLORES-200 and FLEURS test splits into DATA_DIR.

  .venv-bench\\Scripts\\python -I scripts\\bench\\fetch_data.py --out %USERPROFILE%\\tlwu-eval\\bench [--fleurs ru_ru,ja_jp]

FLORES-200 (CC-BY-SA 4.0): https://dl.fbaipublicfiles.com/nllb/flores200_dataset.tar.gz
FLEURS <lang> (CC-BY 4.0): HF dataset google/fleurs, data/<lang>/{test.tsv,audio/test.tar.gz} -> DATA_DIR/fleurs_<xx>
(ru_ru -> fleurs_ru for fleurs_asr.py; ja_jp -> fleurs_ja for make_ja_set.py, #346).
Downloads are treated as untrusted data: archives are extracted with a path-traversal guard.
"""
from __future__ import annotations

import argparse
import tarfile
import urllib.request
from pathlib import Path

FLORES_URL = "https://dl.fbaipublicfiles.com/nllb/flores200_dataset.tar.gz"


def safe_extract(tar_path: Path, dest: Path) -> None:
    dest = dest.resolve()
    with tarfile.open(tar_path) as t:
        for m in t.getmembers():
            tgt = (dest / m.name).resolve()
            if dest not in tgt.parents and tgt != dest or m.issym() or m.islnk() or m.isdev():
                raise SystemExit(f"unsafe archive member: {m.name}")
        t.extractall(dest)


def fleurs_dir(out: Path, code: str) -> Path:
    return out / f"fleurs_{code.split('_')[0]}"


def fetch_fleurs(code: str, out: Path) -> Path:
    fe = fleurs_dir(out, code)
    if not (fe / "test.tsv").exists():
        from huggingface_hub import hf_hub_download

        fe.mkdir(exist_ok=True)
        tsv = hf_hub_download("google/fleurs", f"data/{code}/test.tsv", repo_type="dataset", local_dir=str(fe / "_hf"))
        tgz = hf_hub_download("google/fleurs", f"data/{code}/audio/test.tar.gz", repo_type="dataset",
                              local_dir=str(fe / "_hf"))
        Path(tsv).replace(fe / "test.tsv")
        safe_extract(Path(tgz), fe / "audio")
    return fe


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--fleurs", default="ru_ru", help="comma-separated FLEURS configs (default ru_ru; #346: ja_jp)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    fl = out / "flores200_dataset"
    if not fl.exists():
        tgz = out / "flores200_dataset.tar.gz"
        if not tgz.exists():
            req = urllib.request.Request(FLORES_URL, headers={"User-Agent": "tlwu-bench/0.1"})
            with urllib.request.urlopen(req, timeout=120) as r, open(tgz, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
        safe_extract(tgz, out)
    for code in a.fleurs.split(","):
        fetch_fleurs(code.strip(), out)
    print("flores:", fl, "\nfleurs:", ", ".join(str(fleurs_dir(out, c.strip())) for c in a.fleurs.split(",")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
