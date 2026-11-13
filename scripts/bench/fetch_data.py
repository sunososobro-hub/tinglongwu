#!/usr/bin/env python3
"""Dev tool (NOT part of the offline runtime): download FLORES-200 and FLEURS test splits into DATA_DIR.

  .venv-bench\\Scripts\\python -I scripts\\bench\\fetch_data.py --out %USERPROFILE%\\tlwu-eval\\bench [--fleurs ru_ru,ja_jp]

FLORES-200 (CC-BY-SA 4.0): https://dl.fbaipublicfiles.com/nllb/flores200_dataset.tar.gz
FLEURS <lang> (CC-BY 4.0): HF dataset google/fleurs, data/<lang>/{test.tsv,audio/test.tar.gz} -> DATA_DIR/fleurs_<xx>
(ru_ru -> fleurs_ru for fleurs_asr.py; ja_jp -> fleurs_ja for make_ja_set.py, #346).
--tatoeba-ja N (#429): Tatoeba exports (sentences CC-BY 2.0 FR) + the first N Japanese dialogue recordings that
make_ja_set.py can use (allowed licence + Mandarin translation) -> DATA_DIR/tatoeba_ja/{*.tsv,*.csv,audio/<id>.mp3}.
Downloads are treated as untrusted data: archives are extracted with a path-traversal guard.
"""
from __future__ import annotations

import argparse
import bz2
import importlib.util
import tarfile
import urllib.request
from pathlib import Path

FLORES_URL = "https://dl.fbaipublicfiles.com/nllb/flores200_dataset.tar.gz"
TATOEBA_EXPORTS = "https://downloads.tatoeba.org/exports/"
TATOEBA_FILES = {"jpn_sentences.tsv": "per_language/jpn/jpn_sentences.tsv.bz2",
                 "cmn_sentences.tsv": "per_language/cmn/cmn_sentences.tsv.bz2",
                 "jpn-cmn_links.tsv": "per_language/jpn/jpn-cmn_links.tsv.bz2"}
TATOEBA_AUDIO = "https://tatoeba.org/audio/download/{}"


def download(url: str, dest: Path) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "tlwu-bench/0.1"})
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.replace(dest)
    return dest


def fetch_tatoeba_ja(out: Path, n: int, get=download) -> Path:
    d = out / "tatoeba_ja"
    (d / "audio").mkdir(parents=True, exist_ok=True)
    if not (d / "sentences_with_audio.csv").exists():
        safe_extract(get(TATOEBA_EXPORTS + "sentences_with_audio.tar.bz2", d / "sentences_with_audio.tar.bz2"), d)
    for name, rel in TATOEBA_FILES.items():
        if not (d / name).exists():
            (d / name).write_bytes(bz2.decompress(get(TATOEBA_EXPORTS + rel, d / (name + ".bz2")).read_bytes()))
    spec = importlib.util.spec_from_file_location("make_ja_set", Path(__file__).with_name("make_ja_set.py"))
    mjs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mjs)
    for c in mjs.tatoeba_candidates(out)[:n]:
        mp3 = d / "audio" / f"{c['audio']}.mp3"
        if not mp3.exists():
            get(TATOEBA_AUDIO.format(c["audio"]), mp3)
    return d


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
    ap.add_argument("--fleurs", default="ru_ru", help="comma-separated FLEURS configs (default ru_ru; #346: ja_jp; \"\" = none, #606)")
    ap.add_argument("--tatoeba-ja", type=int, default=0, metavar="N",
                    help="also fetch N Tatoeba Japanese dialogue recordings for make_ja_set.py (#429: 40)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    fl = out / "flores200_dataset"
    if not fl.exists():
        tgz = out / "flores200_dataset.tar.gz"
        if not tgz.exists():
            download(FLORES_URL, tgz)
        safe_extract(tgz, out)
    codes = [c.strip() for c in a.fleurs.split(",") if c.strip()]  # #606: --fleurs "" = FLORES-200 only
    for code in codes:
        fetch_fleurs(code, out)
    print("flores:", fl, "\nfleurs:", ", ".join(str(fleurs_dir(out, c)) for c in codes) or "-")
    if a.tatoeba_ja > 0:
        print("tatoeba:", fetch_tatoeba_ja(out, a.tatoeba_ja))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
