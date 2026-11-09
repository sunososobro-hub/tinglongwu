"""Model downloader (tlwu-setup). Part of tinglongwu_setup, the ONLY networked code in this repo.

Pin-on-first-download, verify thereafter: models/manifest.json records, per model,
the pinned source (HF commit sha / package URL) and SHA256 of every file. If a model
directory already exists and is in the manifest, it is only re-verified (no network).
Part of tinglongwu_setup, the only networked package. Does NOT import `tinglongwu` (which blocks the network).

  tlwu-setup --whisper small --langs ru,ja --to zh,ko
      --langs X,Y : source languages -> fetches X->en     --to A,B : targets -> fetches en->A
      --pair s-t  : one explicit pair (repeatable)       --m2m : M2M-100 1.2B direct translation (MIT; conversion needs torch+transformers, see get_m2m)
  tlwu-setup --qwen           # Qwen3-8B/4B Q4_K_M GGUF for the default LLM engine
  tlwu-setup --verify          # offline integrity check
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # repo root  # repo root
ARGOS_INDEX = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"


def _open(url: str, timeout: int = 60):
    # argos-net.com returns 403 for urllib's default User-Agent
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "tlwu-setup/0.1"}),
                                  timeout=timeout)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def hash_tree(d: Path) -> dict[str, str]:
    return {p.relative_to(d).as_posix(): sha256(p) for p in sorted(d.rglob("*"))
            if p.is_file() and ".cache" not in p.parts}


def pin_or_verify(man: dict, key: str, d: Path, source: dict) -> None:
    files = hash_tree(d)
    if key in man:
        if man[key]["files"] != files:
            bad = [k for k in set(files) | set(man[key]["files"])
                   if files.get(k) != man[key]["files"].get(k)]
            sys.exit(f"SHA256 MISMATCH for {key}: {bad[:5]} - refusing. Delete {d} to re-download.")
        print(f"verified {key} ({len(files)} files)")
    else:
        man[key] = {**source, "files": files}
        print(f"pinned {key} ({len(files)} files)")


WHISPER_REPOS = {"large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo"}  # MIT; others: Systran


def whisper_repo(size: str) -> str:
    return WHISPER_REPOS.get(size, f"Systran/faster-whisper-{size}")


def get_whisper(man, mdir, size):
    key, dest = f"whisper-{size}", mdir / f"whisper-{size}"
    if not (dest.exists() and key in man):
        from huggingface_hub import HfApi, snapshot_download

        repo = whisper_repo(size)
        rev = man.get(key, {}).get("revision") or HfApi().model_info(repo).sha
        snapshot_download(repo, revision=rev, local_dir=str(dest))
        src = {"repo": repo, "revision": rev}
    else:
        src = man[key]
    pin_or_verify(man, key, dest, {k: src[k] for k in ("repo", "revision")})


def get_pair(man, mdir, pair):
    s, t = pair.split("-")
    key, dest = f"opus-{s}-{t}", mdir / "opus" / pair
    if dest.exists() and key in man:
        return pin_or_verify(man, key, dest, {})
    url = man.get(key, {}).get("url")
    if not url:
        with _open(ARGOS_INDEX) as r:
            index = json.load(r)
        hits = [e for e in index if e.get("from_code") == s and e.get("to_code") == t]
        if not hits or not hits[0].get("links"):
            sys.exit(f"No Argos package for {s}->{t}")
        url = hits[0]["links"][0]
    with tempfile.TemporaryDirectory() as tmp:
        z = Path(tmp) / "pkg.zip"
        with _open(url, 300) as r, open(z, "wb") as f:
            shutil.copyfileobj(r, f)
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():  # zip-slip guard
                if Path(n).is_absolute() or ".." in Path(n).parts:
                    sys.exit(f"unsafe path in archive: {n}")
            zf.extractall(Path(tmp) / "x")
        roots = [p.parent for p in (Path(tmp) / "x").rglob("sentencepiece.model")]
        if not roots:
            sys.exit("unexpected package layout (no sentencepiece.model)")
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        keep = {"model", "sentencepiece.model", "metadata.json"}  # drop stanza/ sentence-boundary data
        dest.mkdir()
        for item in roots[0].iterdir():
            if item.name in keep:
                (shutil.copytree if item.is_dir() else shutil.copy2)(item, dest / item.name)
    pin_or_verify(man, key, dest, {"url": url})


def get_m2m(man, mdir, size="1.2B"):
    """M2M-100 1.2B (MIT): download facebook/m2m100_1.2B, convert to CTranslate2 int8 -> models/m2m-1.2B/.

    Conversion needs torch + transformers (dev-only, NOT runtime deps). They may live in another
    interpreter: set TLWU_CONVERTER_PYTHON to its python.exe.
    """
    import subprocess

    key, dest = f"m2m-{size}", mdir / f"m2m-{size}"
    if dest.exists() and key in man:
        return pin_or_verify(man, key, dest, {k: man[key][k] for k in ("repo", "revision")})
    py = os.environ.get("TLWU_CONVERTER_PYTHON", sys.executable)
    chk = subprocess.run([py, "-c", "import torch, transformers, ctranslate2"], capture_output=True)
    if chk.returncode:
        sys.exit("M2M-100 conversion needs torch + transformers + ctranslate2 in one interpreter "
                 "(dev-only; a throwaway venv is fine): pip install torch transformers ctranslate2 sentencepiece, "
                 "then set TLWU_CONVERTER_PYTHON=<that python> and rerun.")
    from huggingface_hub import HfApi, snapshot_download

    repo = f"facebook/m2m100_{size}"
    rev = man.get(key, {}).get("revision") or HfApi().model_info(repo).sha
    files = ["sentencepiece.bpe.model", "vocab.json", "tokenizer_config.json", "special_tokens_map.json"]
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "hf"
        snapshot_download(repo, revision=rev, local_dir=str(src), allow_patterns=[
            "config.json", "generation_config.json", "pytorch_model.bin", *files])
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        subprocess.check_call([py, "-m", "ctranslate2.converters.transformers", "--model", str(src),
                               "--output_dir", str(dest / "model"), "--quantization", "int8",
                               "--copy_files", *files[1:]])
        shutil.copy2(src / "sentencepiece.bpe.model", dest / "sentencepiece.bpe.model")
    pin_or_verify(man, key, dest, {"repo": repo, "revision": rev})


QWEN_GGUF = {  # key == models/ subdirectory; Apache-2.0 GGUFs from the official Qwen repos
    "qwen3-8b": ("Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf"),
    "qwen3-4b": ("Qwen/Qwen3-4B-GGUF", "Qwen3-4B-Q4_K_M.gguf"),
}


def get_qwen(man, mdir, name):
    """Qwen3 Q4_K_M GGUF -> models/<name>/<file>.gguf (pinned by HF commit sha + SHA256)."""
    repo, fname = QWEN_GGUF[name]
    dest = mdir / name
    if not (dest.exists() and name in man):
        from huggingface_hub import HfApi, snapshot_download

        rev = man.get(name, {}).get("revision") or HfApi().model_info(repo).sha
        snapshot_download(repo, revision=rev, local_dir=str(dest), allow_patterns=[fname])
        src = {"repo": repo, "revision": rev}
    else:
        src = man[name]
    pin_or_verify(man, name, dest, {k: src[k] for k in ("repo", "revision")})


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--whisper", action="append", default=[],
                   choices=["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"])
    p.add_argument("--pair", action="append", default=[], help="e.g. ru-en, en-zh")
    p.add_argument("--langs", default="", help="comma list of source langs (downloads X->en)")
    p.add_argument("--to", default="", help="comma list of target langs (downloads en->X)")
    p.add_argument("--m2m", action="store_true",
                   help="M2M-100 1.2B direct translation (MIT): download + convert with ct2-transformers-converter")
    p.add_argument("--qwen", action="store_true",
                   help="Qwen3-8B + Qwen3-4B Q4_K_M GGUF for --engine auto/qwen/qwen4b (~7.5 GB download)")
    p.add_argument("--models-dir", default=os.environ.get("TLWU_MODELS") or str(ROOT / "models"))
    p.add_argument("--verify", action="store_true", help="only verify existing models (offline)")
    a = p.parse_args(argv)
    mdir = Path(a.models_dir)
    mdir.mkdir(parents=True, exist_ok=True)
    mf = mdir / "manifest.json"
    man = json.loads(mf.read_text("utf-8")) if mf.exists() else {}
    if a.verify:
        for key in man:
            d = mdir / ("opus/" + key[5:] if key.startswith("opus-") else key)
            pin_or_verify(man, key, d, {})
        return 0
    for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        os.environ.pop(k, None)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    for s in a.whisper:
        get_whisper(man, mdir, s)
        mf.write_text(json.dumps(man, indent=1), "utf-8")
    if a.m2m:
        get_m2m(man, mdir)
        mf.write_text(json.dumps(man, indent=1), "utf-8")
    if a.qwen:
        for name in QWEN_GGUF:
            get_qwen(man, mdir, name)
            mf.write_text(json.dumps(man, indent=1), "utf-8")
    pairs = (a.pair + [f"{x}-en" for x in a.langs.split(",") if x and x != "en"]
             + [f"en-{x}" for x in a.to.split(",") if x and x != "en"])
    for pr in dict.fromkeys(pairs):
        get_pair(man, mdir, pr)
        mf.write_text(json.dumps(man, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
