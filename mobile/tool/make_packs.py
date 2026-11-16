"""Builds the optional pack zips of assets/voice_packs.json for the in-app "Import models" screen:
tlwu-voice-en.zip, tlwu-voice-ru.zip and tlwu-mobile-lang-ru.zip.

Each pack names its upstream archive (`source`). This script downloads it when WORKDIR has no
unpacked copy yet, checks EVERY file against the manifest (size + SHA-256, so the zip always
matches what the app accepts) and writes the zip: entries STORED (the weights do not compress,
and the iOS importer reads stored zips only) under <zip name>/, with the file names the app
expects (`model.onnx` for upstream `vits-ljs.onnx`), plus a NOTICE.txt and the upstream
LICENSE/README files.

    python3 -I tool/make_packs.py <WORKDIR> <OUT_DIR> [pack id ...]

Upload (owner): gh release upload mobile-latest <OUT_DIR>/tlwu-*.zip --clobber
"""
import hashlib
import json
import os
import sys
import tarfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PACKS_JSON = os.path.join(HERE, "..", "assets", "voice_packs.json")

NOTICES = {
    "voice-en": """English read-aloud voice for tinglongWu 對話翻譯.
sherpa-onnx vits-ljs (VITS, LJSpeech, female, 22.05 kHz), unmodified upstream files.
Licence: Apache-2.0 (model card), LJSpeech dataset public domain, lexicon from the CMU
Pronouncing Dictionary (BSD-style).""",
    "voice-ru": """Russian read-aloud voice for tinglongWu 對話翻譯.
Supertonic 3 (Supertone Inc.), int8 ONNX export by sherpa-onnx, unmodified upstream files.
Model licence: BigScience OpenRAIL-M, https://huggingface.co/Supertone/supertonic-3/blob/main/LICENSE
Sample code licence: MIT (LICENSE in this folder).""",
    "lang-ru": """Russian speech recognition for tinglongWu 對話翻譯.
sherpa-onnx zipformer transducer (int8) converted from the Vosk Russian model,
https://huggingface.co/alphacep/vosk-model-ru, unmodified upstream files.
Licence: Apache-2.0.""",
}
EXTRA_UPSTREAM = ("LICENSE", "README.md", "MODEL_CARD")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def unpack(url, workdir):
    """Returns the folder of the upstream archive `url` under workdir (downloaded on demand)."""
    stem = url.split("/")[-1].split(".tar")[0]
    folder = os.path.join(workdir, stem)
    if not os.path.isdir(folder):
        os.makedirs(workdir, exist_ok=True)
        archive = os.path.join(workdir, url.split("/")[-1])
        if not os.path.exists(archive):
            print("download", url)
            urllib.request.urlretrieve(url, archive)
        with tarfile.open(archive) as t:
            t.extractall(workdir, filter="data")
    return folder


def build(pack, workdir, out_dir):
    folder = unpack(pack["source"], workdir)
    prefix = pack["name"][: -len(".zip")] + "/"
    entries = []
    for e in pack["files"]:
        base = e["path"].split("/")[-1]
        src = os.path.join(folder, e.get("alias", base))
        if not os.path.exists(src):
            src = os.path.join(folder, base)
        size, digest = os.path.getsize(src), sha256(src)
        if size != e["size"] or digest != e["sha256"]:
            sys.exit(f"{pack['id']}: {src}: size/sha256 {size} {digest} do not match the manifest")
        print("ok", pack["id"], e["path"])
        entries.append((prefix + base, src))
    for n in EXTRA_UPSTREAM:
        if os.path.exists(os.path.join(folder, n)):
            entries.append((prefix + "UPSTREAM-" + n, os.path.join(folder, n)))
    out = os.path.join(out_dir, pack["name"])
    os.makedirs(out_dir, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        z.writestr(
            prefix + "NOTICE.txt",
            NOTICES[pack["id"]] + "\n\nNo need to unzip: in the app open \"Import models\" and pick this zip.\n",
        )
        for name, src in entries:
            z.write(src, name)
    print(out, os.path.getsize(out), sha256(out))
    return out


def main(workdir, out_dir, ids):
    with open(PACKS_JSON, encoding="utf-8") as f:
        packs = json.load(f)["packs"]
    want = [p for p in packs if not ids or p["id"] in ids]
    if not want:
        sys.exit("no such pack: " + " ".join(ids))
    outs = [build(p, workdir, out_dir) for p in want]
    print("\ngh release upload mobile-latest " + " ".join(f'"{o}"' for o in outs) + " --clobber")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
