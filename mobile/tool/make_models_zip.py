"""Builds tlwu-mobile-models.zip (the models bundle for the in-app "Import models" screen).

Every manifest file is checked against assets/models_manifest.json (size + SHA-256) before it
goes in, so the zip always matches what the app accepts. Entries are STORED (no compression;
the weights do not compress) under tlwu-mobile-models/, plus the upstream license texts.

    bash tool/fetch_models.sh <DIR>
    python3 -I tool/make_models_zip.py <DIR>/models <DIR>/licenses <OUT.zip>

MODELS_DIR has gigaam3/, silero_vad.onnx and Hy-MT2-1.8B-Q4_K_M.gguf (same layout as on the phone).
LICENSE_DIR has GigaAM-LICENSE.txt, silero-vad-LICENSE.txt and Hy-MT2-LICENSE.txt.
"""
import hashlib
import json
import os
import sys
import zipfile

PREFIX = "tlwu-mobile-models/"
NOTICE = """tinglongWu mobile models bundle

No need to unzip: in the app, open "Import models" and pick this zip.
All files are unmodified upstream files.

- gigaam3/  GigaAM v3 Russian ASR (salute-developers/GigaAM), ONNX export by sherpa-onnx
  (k2-fsa/sherpa-onnx release asr-models, sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16).
  License: MIT, see LICENSES/GigaAM-LICENSE.txt.
- silero_vad.onnx  Silero VAD (snakers4/silero-vad), from the sherpa-onnx asr-models release.
  License: MIT, see LICENSES/silero-vad-LICENSE.txt.
- Hy-MT2-1.8B-Q4_K_M.gguf  Tencent Hy-MT2-1.8B (tencent/Hy-MT2-1.8B-GGUF on Hugging Face).
  License: Apache-2.0, see LICENSES/Hy-MT2-LICENSE.txt.
"""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main(models, lic, out):
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "assets", "models_manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    files = [e for e in manifest["files"] if not e.get("url")]  # url = official link, not bundled
    for e in files:
        p = os.path.join(models, e["path"])
        size, digest = os.path.getsize(p), sha256(p)
        if size != e["size"] or digest != e["sha256"]:
            sys.exit(f"{e['path']}: size/sha256 {size} {digest} do not match the manifest")
        print("ok", e["path"])
    extra = {
        "LICENSES/" + n: os.path.join(lic, n)
        for n in ("GigaAM-LICENSE.txt", "silero-vad-LICENSE.txt", "Hy-MT2-LICENSE.txt")
    }
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        z.writestr(PREFIX + "NOTICE.txt", NOTICE)
        for name, src in extra.items():
            z.write(src, PREFIX + name)
        for e in files:
            z.write(os.path.join(models, e["path"]), PREFIX + e["path"])
    print(out, os.path.getsize(out), sha256(out))


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
