#!/usr/bin/env python3
"""Regenerate requirements.lock.txt: every requirement in requirements.txt (+ marker-only extras) with all
PyPI-published sha256 hashes for the pinned version (read-only network). Then:
    pip install --require-hashes -r requirements.lock.txt
"""
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# transitive deps that only apply on some platforms/Pythons (so absent from a plain freeze)
EXTRA = ["colorama==0.4.6 ; sys_platform == 'win32'", "tomli==2.2.1 ; python_version < '3.11'"]


def main() -> None:
    reqs = [ln.strip() for ln in (ROOT / "requirements.txt").read_text("utf-8").splitlines()
            if "==" in ln and not ln.lstrip().startswith("#")] + EXTRA
    out = []
    for r in sorted(reqs, key=str.lower):
        spec, _, marker = r.partition(";")
        n, v = spec.strip().split("==")
        with urllib.request.urlopen(f"https://pypi.org/pypi/{n}/{v}/json", timeout=60) as f:
            d = json.load(f)
        hs = sorted({u["digests"]["sha256"] for u in d["urls"]})
        head = f"{n}=={v}" + (f" ; {marker.strip()}" if marker else "")
        out.append(head + " \\\n" + " \\\n".join(f"    --hash=sha256:{h}" for h in hs))
    (ROOT / "requirements.lock.txt").write_text(
        "# Hash-pinned lock of requirements.txt (all hashes PyPI publishes for each pinned version,\n"
        "# so it works on any OS/Python). Install with:\n"
        "#   pip install --require-hashes -r requirements.lock.txt\n"
        "# Regenerate: python scripts/make_lock.py\n" + "\n".join(out) + "\n", "utf-8", newline="\n")


if __name__ == "__main__":
    main()
