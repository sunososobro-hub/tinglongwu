#!/usr/bin/env python3
"""Package the self-built llama.cpp .so files into native/llama-cpp-dart-selfbuilt.aar (#1511).

    python3 tool/pack_llama_aar.py STAGE OUT

STAGE holds AndroidManifest.xml, R.txt and jni/<abi>/*.so for every ABI that was built
(arm64-v8a always; x86_64 only for the emulator debug build). Every ABI folder found is packed.
"""
import os
import sys
import zipfile

KNOWN_ABIS = ("arm64-v8a", "x86_64")


def abis_in(stage):
    jni = os.path.join(stage, "jni")
    found = sorted(d for d in os.listdir(jni) if os.path.isdir(os.path.join(jni, d))) if os.path.isdir(jni) else []
    bad = [a for a in found if a not in KNOWN_ABIS]
    if bad:
        raise ValueError("unknown ABI folder(s) in %s: %s" % (jni, ", ".join(bad)))
    empty = [a for a in found if not any(f.endswith(".so") for f in os.listdir(os.path.join(jni, a)))]
    if not found or empty:
        raise ValueError("no .so files for: %s" % (", ".join(empty) or "any ABI"))
    return found


def pack(stage, out):
    abis = abis_in(stage)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("classes.jar", b"PK\x05\x06" + b"\x00" * 18)  # empty jar
        z.write(os.path.join(stage, "AndroidManifest.xml"), "AndroidManifest.xml")
        z.write(os.path.join(stage, "R.txt"), "R.txt")
        for abi in abis:
            d = os.path.join(stage, "jni", abi)
            for f in sorted(os.listdir(d)):
                z.write(os.path.join(d, f), "jni/%s/%s" % (abi, f))
    return abis


def main(argv):
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        abis = pack(argv[1], argv[2])
    except ValueError as e:
        print("pack_llama_aar: %s" % e, file=sys.stderr)
        return 1
    print("packed %s: %s" % (argv[2], " ".join(abis)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
