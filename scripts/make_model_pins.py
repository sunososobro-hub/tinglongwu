#!/usr/bin/env python3
"""Regenerate tinglongwu_setup/manifest.json (committed model pins). Maintainer tool; needs network.

HF repos: current commit sha from the Hub API; SHA256 of LFS files from the API, of small non-LFS files by
downloading them at that exact revision. Argos packages: the .argosmodel archive is downloaded, hashed, and
the extracted layout (same code as tlwu-setup) hashed. Review the diff before committing: a changed hash
means upstream changed (or was tampered with).

    python scripts/make_model_pins.py            # refresh everything
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tinglongwu_setup import downloader as dl  # noqa: E402

WHISPER = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
# Hugging Face's own SFconvertbot PR (main has only pickle weights); pinned by commit sha.
M2M = ("facebook/m2m100_1.2B", "0d54e885bd182833d3b4342a9dd270fbb312d424",
       ["config.json", "generation_config.json", "model.safetensors", "sentencepiece.bpe.model", "vocab.json",
        "tokenizer_config.json", "special_tokens_map.json"])
ARGOS = {  # key -> package URL (taken from the Argos index once; pinned here from now on)
    "opus-ru-en": "https://argos-net.com/v1/translate-ru_en-1_9.argosmodel",
    "opus-en-zh": "https://argos-net.com/v1/translate-en_zh-1_9.argosmodel",
}


def hf_pin(api, repo, rev=None, only=None):
    info = api.model_info(repo, revision=rev, files_metadata=True)
    rev = info.sha
    files = {}
    for f in info.siblings:
        if only is not None and f.rfilename not in only:
            continue
        if f.lfs:
            files[f.rfilename] = f.lfs.sha256
        else:
            from huggingface_hub import hf_hub_download

            with tempfile.TemporaryDirectory() as t:
                files[f.rfilename] = dl.sha256(Path(hf_hub_download(repo, f.rfilename, revision=rev, local_dir=t)))
    return {"repo": repo, "revision": rev, "files": dict(sorted(files.items()))}


def main():
    import os

    os.environ.pop("HF_HUB_OFFLINE", None)
    from huggingface_hub import HfApi

    api = HfApi()
    models = {f"whisper-{s}": hf_pin(api, dl.whisper_repo(s)) for s in WHISPER}
    for name, (repo, fname) in dl.QWEN_GGUF.items():
        models[name] = hf_pin(api, repo, only={fname})
    repo, rev, only = M2M
    models["m2m-1.2B"] = {**hf_pin(api, repo, rev, set(only)), "note": "SFconvertbot PR refs/pr/7 (safetensors)"}
    argos = {}
    for key, url in ARGOS.items():
        with tempfile.TemporaryDirectory() as t:
            z = Path(t) / "pkg.zip"
            with dl._open(url, 300) as r, open(z, "wb") as f:
                f.write(r.read())
            out = Path(t) / "out"
            dl.extract_argos(z, out, Path(t) / "x")
            argos[key] = {"url": url, "archive_sha256": dl.sha256(z), "files": dl.hash_tree(out)}
    dl.PINS_FILE.write_text(json.dumps({"models": models, "argos": argos}, indent=1) + "\n", "utf-8", newline="\n")
    print("wrote", dl.PINS_FILE)


if __name__ == "__main__":
    main()
