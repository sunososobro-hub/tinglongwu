"""Sign SHA256SUMS.txt for the self-updater (#1348). Pure Python, no new dependency.

  python scripts/sign_sums.py keygen
      print a new secret key (hex, store it ONLY as the repo secret TLWU_SUMS_SIGNING_KEY) and its public key (hex,
      paste into SUMS_PUBKEY in tinglongwu_setup/selfupdate.py). Run it on your own machine, never in CI.
  python scripts/sign_sums.py sign FILE
      write FILE.sig (base64 Ed25519 signature of FILE's exact bytes) with the key in $TLWU_SUMS_SIGNING_KEY.
      No key in the environment -> prints a notice and exits 0 without writing (releases before the key existed).
  python scripts/sign_sums.py verify FILE PUBKEY_HEX
      exit 0 when FILE.sig is a valid signature of FILE by PUBKEY_HEX, else 1.
"""
from __future__ import annotations

import base64
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "system"))  # #1724: tinglongwu_setup lives in src/system (#1664)
from tinglongwu_setup import ed25519, selfupdate  # noqa: E402

ENV_KEY = "TLWU_SUMS_SIGNING_KEY"


def main(argv: list[str]) -> int:
    if argv[:1] == ["keygen"]:
        sk = secrets.token_bytes(32)
        print(f"secret (repo secret {ENV_KEY}): {sk.hex()}")
        print(f"public (SUMS_PUBKEY):          {ed25519.public_key(sk).hex()}")
        return 0
    if len(argv) == 2 and argv[0] == "sign":
        key = os.environ.get(ENV_KEY, "").strip()
        if not key:
            print(f"::notice::{ENV_KEY} is not set: {argv[1]} stays unsigned")
            return 0
        try:
            sk = bytes.fromhex(key)
            sig = ed25519.sign(sk, Path(argv[1]).read_bytes())
        except ValueError:
            print(f"::error::{ENV_KEY} must be 64 hex characters (32 bytes)")
            return 1
        out = Path(argv[1] + ".sig")
        out.write_bytes(base64.b64encode(sig) + b"\n")
        print(f"signed {argv[1]} -> {out.name} (public key {ed25519.public_key(sk).hex()})")
        return 0
    if len(argv) == 3 and argv[0] == "verify":
        data, sig = Path(argv[1]).read_bytes(), Path(argv[1] + ".sig").read_bytes()
        try:
            selfupdate.verify_sums(data, sig, argv[2])
        except ValueError as e:
            print(f"FAIL {e}")
            return 1
        print("OK")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
