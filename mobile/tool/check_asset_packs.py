#!/usr/bin/env python3
"""CI-independent check that the Play asset packs of an AAB are stable and patchable (#1169).

    python3 -I tool/check_asset_packs.py check   <app.aab>
    python3 -I tool/check_asset_packs.py compare <old.aab> <new.aab>
    python3 -I tool/check_asset_packs.py build   [--build-numbers 9001 9002]   (run in mobile/, needs the packs filled)

check:   every bundled model file (assets/models_manifest.json, entries without `url`) is in its
         pack as <pack>/assets/<path> with the manifest size + SHA-256, and BundleConfig.pb lists its
         extension in compression.uncompressedGlob (so the pack APKs keep it STORED, not deflated).
         (The AAB zip itself always deflates its entries; the glob is what bundletool honours.)
compare: the pack entries (names, order, sizes, CRC32, compression method, timestamps, SHA-256 of
         the content) and the pack manifests of two AABs are identical, i.e. an app-code-only
         update leaves models_asr / models_llm untouched and Play has nothing to re-send.
build:   builds the AAB twice with different build numbers (same sources), then runs check + compare.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
MOBILE = os.path.dirname(HERE)
MANIFEST = os.path.join(MOBILE, "assets", "models_manifest.json")
PACK_OF_GROUP = {"asr": "models_asr", "vad": "models_asr", "llm": "models_llm"}
PACKS = ("models_asr", "models_llm")
AAB = os.path.join(MOBILE, "build", "app", "outputs", "bundle", "release", "app-release.aab")


def sha256_of(zf, info):
    h = hashlib.sha256()
    with zf.open(info) as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def pack_entries(zf):
    """Pack files in AAB order, plus the pack manifests."""
    out = []
    for i in zf.infolist():
        top = i.filename.split("/", 1)[0]
        if top in PACKS and not i.is_dir():
            out.append(i)
    return out


def uncompressed_exts(zf):
    """Extensions kept uncompressed by BundleConfig.pb (`**[gG][gG][uU][fF]` -> `gguf`, `**.onnx` -> `onnx`)."""
    exts = set()
    for g in re.findall(rb"\*\*[ -~]+", zf.read("BundleConfig.pb")):
        g = re.sub(r"\[(\w)\w\]", lambda m: m.group(1).lower(), g.decode()[2:])
        exts.add(g.lstrip(".").lower())
    return exts


def check(path):
    problems = []
    with open(MANIFEST, encoding="utf-8") as f:
        files = json.load(f)["files"]
    with zipfile.ZipFile(path) as zf:
        by_name = {i.filename: i for i in zf.infolist()}
        keep = uncompressed_exts(zf)
        for e in files:
            if e.get("url"):
                continue
            pack = PACK_OF_GROUP.get(e.get("group"))
            name = f"{pack}/assets/{e['path']}"
            i = by_name.get(name)
            if i is None:
                problems.append(f"{name}: missing")
                continue
            ext = e["path"].rsplit(".", 1)[-1].lower()
            if ext not in keep:
                problems.append(f"{name}: '.{ext}' is not in uncompressedGlob (set androidResources.noCompress)")
            if i.file_size != e["size"]:
                problems.append(f"{name}: size {i.file_size} != manifest {e['size']}")
            elif sha256_of(zf, i) != e["sha256"]:
                problems.append(f"{name}: SHA-256 differs from the manifest")
    return problems


def describe(zf):
    d = {}
    order = []
    for i in pack_entries(zf):
        order.append(i.filename)
        if i.filename.endswith("AndroidManifest.xml"):
            data = hashlib.sha256(zf.read(i)).hexdigest()
        else:
            data = sha256_of(zf, i)
        d[i.filename] = (i.file_size, i.compress_size, i.CRC, i.compress_type, i.date_time, data)
    return order, d


def compare(a, b):
    problems = []
    with zipfile.ZipFile(a) as za, zipfile.ZipFile(b) as zb:
        oa, da = describe(za)
        ob, db = describe(zb)
    if not oa:
        problems.append("no pack entries found in the first AAB")
    if oa != ob:
        problems.append("pack entry names or order differ")
    for n in sorted(set(da) | set(db)):
        if da.get(n) != db.get(n):
            problems.append(f"{n}: {da.get(n)} != {db.get(n)}")
    return problems


def report(title, problems):
    if problems:
        print(f"FAIL {title}")
        for p in problems:
            print("  -", p)
        return False
    print(f"OK   {title}")
    return True


def build(numbers):
    outs = []
    for n in numbers:
        subprocess.run(["flutter", "build", "appbundle", "--release", "--target-platform", "android-arm64",
                        "--build-number", str(n)], cwd=MOBILE, check=True, shell=(os.name == "nt"),
                       env={**os.environ, "TLWU_PLAY_PACKS": "1"})  # #1169: the packs are opt-in in Gradle
        dst = os.path.join(MOBILE, "build", f"check-{n}.aab")
        shutil.copyfile(AAB, dst)
        outs.append(dst)
    ok = all([report(f"check {o}", check(o)) for o in outs])
    ok = report("compare", compare(*outs)) and ok
    return ok


def main(argv):
    if len(argv) >= 3 and argv[1] == "check":
        ok = report("check", check(argv[2]))
    elif len(argv) == 4 and argv[1] == "compare":
        ok = report("compare", compare(argv[2], argv[3]))
    elif argv[1:2] == ["build"]:
        nums = argv[3:5] if argv[2:3] == ["--build-numbers"] and len(argv) == 5 else ["9001", "9002"]
        ok = build(nums)
    else:
        sys.exit(__doc__)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv)
