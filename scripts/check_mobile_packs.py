#!/usr/bin/env python3
"""#1510: fail when the `mobile-latest` release lacks an expected mobile pack zip.

Usage:
  python scripts/check_mobile_packs.py                 # asks `gh release view mobile-latest`
  python scripts/check_mobile_packs.py --assets-json F # F: `gh release view mobile-latest --json assets` output
                                                       #    or a JSON list of asset names
Exit 0 when every expected zip is listed, 1 (naming each missing file) otherwise.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

EXPECTED = ("tlwu-voice-en.zip", "tlwu-voice-ru.zip", "tlwu-mobile-lang-ru.zip")


def asset_names(data) -> set[str]:
    """Accept {"assets": [{"name": ...}]}, [{"name": ...}] or ["name", ...]."""
    items = data.get("assets", []) if isinstance(data, dict) else data
    return {(i.get("name") if isinstance(i, dict) else str(i)) for i in items}


def missing(names, expected=EXPECTED) -> list[str]:
    have = set(names)
    return [e for e in expected if e not in have]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets-json")
    ap.add_argument("--tag", default="mobile-latest")
    a = ap.parse_args(argv)
    if not a.tag.strip():
        ap.error("--tag must not be empty")
    if a.assets_json:
        with open(a.assets_json, encoding="utf-8") as f:
            data = json.load(f)
    else:
        out = subprocess.run(["gh", "release", "view", a.tag, "--json", "assets"],
                             check=True, stdout=subprocess.PIPE, text=True).stdout
        data = json.loads(out)
    gone = missing(asset_names(data))
    for g in gone:
        print(f"missing from {a.tag}: {g}")
    if not gone:
        print("all mobile packs present")
    return 1 if gone else 0


if __name__ == "__main__":
    sys.exit(main())
