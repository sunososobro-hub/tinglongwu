#!/usr/bin/env python3
"""Regenerate tinglongwu_setup/manifest.json (committed model pins). Maintainer tool; needs network.

HF repos: current commit sha from the Hub API; SHA256 of LFS files from the API, of small non-LFS files by
downloading them at that exact revision. Argos packages: the .argosmodel archive is downloaded, hashed, and
the extracted layout (same code as tlwu-setup) hashed. Review the diff before committing: a changed hash
means upstream changed (or was tampered with).

    python scripts/make_model_pins.py            # refresh everything
    python scripts/make_model_pins.py --only whisper-cpp-   # refresh only the keys with this prefix (#1401);
                                                            # every other committed pin is kept byte-for-byte
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "system"))  # #1664: tinglongwu_setup lives in src/system/
from tinglongwu_setup import downloader as dl  # noqa: E402

WHISPER = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
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


def refresh_only(api, prefix):
    """#1401: re-pin only the model keys starting with `prefix`; keep every other committed pin unchanged."""
    pins = dl.load_pins()
    wanted = {f"whisper-cpp-{s}": (lambda s=s: hf_pin(api, dl.WHISPER_CPP_REPO, only={dl.whisper_cpp_file(s)}))
              for s in dl.WHISPER_CPP_SIZES}
    wanted.update({f"whisper-{s}": (lambda s=s: hf_pin(api, dl.whisper_repo(s))) for s in WHISPER})
    keys = sorted(k for k in wanted if k.startswith(prefix))
    if not keys:
        sys.exit(f"--only {prefix!r} matches no refreshable model key")
    models = dict(pins.get("models", {}))
    for k in keys:
        models[k] = wanted[k]()
        if not models[k]["files"]:
            sys.exit(f"{k}: no matching file in {models[k]['repo']}@{models[k]['revision']}")
    pins["models"] = models
    pins.setdefault("argos", {})
    dl.PINS_FILE.write_text(json.dumps(pins, indent=1) + "\n", "utf-8", newline="\n")
    return keys


def main(argv=None):
    import os

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", metavar="PREFIX", help="refresh only model keys with this prefix, e.g. whisper-cpp-")
    a = ap.parse_args(argv)
    if a.only is not None and not a.only:
        ap.error("--only needs a non-empty prefix (omit it to refresh everything)")
    os.environ.pop("HF_HUB_OFFLINE", None)
    from huggingface_hub import HfApi

    api = HfApi()
    if a.only is not None:
        print("refreshed", ", ".join(refresh_only(api, a.only)), "in", dl.PINS_FILE)
        return
    models = {f"whisper-{s}": hf_pin(api, dl.whisper_repo(s)) for s in WHISPER}
    for s in dl.WHISPER_CPP_SIZES:  # #1401: whisper.cpp ggml models for the Vulkan ASR (LFS -> SHA256 from the API)
        models[f"whisper-cpp-{s}"] = hf_pin(api, dl.WHISPER_CPP_REPO, only={dl.whisper_cpp_file(s)})
    models[dl.HYMT_DIR] = hf_pin(api, dl.HYMT_GGUF[0], only={dl.HYMT_GGUF[1]})
    for voice, files in dl.PIPER_RU.items():
        models[dl.piper_ru_dir(voice)] = hf_pin(api, dl.PIPER_REPO, only=set(files))
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
