#!/usr/bin/env python3
"""mobile-apk.yml helper (#1511): fail unless an APK / AAB ships native libs for exactly the given ABIs.

    python3 scripts/check_apk_abis.py FILE.apk|FILE.aab ABI[,ABI...]

APK native libs sit under lib/<abi>/, AAB ones under <module>/lib/<abi>/. The release APK and AAB must stay
arm64-v8a only; the emulator debug APK must carry arm64-v8a and x86_64. Prints the ABI list and exits 1 on a mismatch.
"""
import sys
import zipfile


def abis(path):
    """{abi: number of .so files} for every lib/<abi>/ folder in the archive."""
    out = {}
    depth = 4 if path.lower().endswith(".aab") else 3  # <module>/lib/<abi>/<f> vs lib/<abi>/<f>
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            parts = name.split("/")
            if len(parts) == depth and parts[-3] == "lib" and parts[-1]:
                abi = parts[-2]
                out[abi] = out.get(abi, 0) + (1 if name.endswith(".so") else 0)
    return out


def check(path, expected):
    found = abis(path)
    want = sorted(set(expected))
    errors = []
    if sorted(found) != want:
        errors.append("ABIs %s, expected %s" % (",".join(sorted(found)) or "none", ",".join(want)))
    errors += ["lib/%s has no .so" % a for a in want if a in found and found[a] == 0]
    return found, errors


def main(argv):
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    path, expected = argv[1], [a for a in argv[2].split(",") if a]
    found, errors = check(path, expected)
    print("%s: %s" % (path, ", ".join("%s (%d .so)" % (a, n) for a, n in sorted(found.items())) or "no native libs"))
    for e in errors:
        print("ABI check failed: %s" % e, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
