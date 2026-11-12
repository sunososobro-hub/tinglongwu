"""#422: every committed SHA256 pin is 64 lowercase hex chars (a lost digit made every fresh Kokoro download fail)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from tinglongwu_setup import downloader

HEX64 = re.compile(r"[0-9a-f]{64}")


def _shas(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _shas(v, f"{path}/{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _shas(v, f"{path}[{i}]")
    elif isinstance(obj, str) and re.fullmatch(r"[0-9a-fA-F]{40,80}", obj) and "revision" not in path:
        yield path, obj


def test_downloader_constant_pins_are_sha256():
    tables = {n: v for n, v in vars(downloader).items() if n.isupper() and isinstance(v, dict)}
    found = [(f"{n}{p}", s) for n, t in tables.items() for p, s in _shas(t)]
    assert any("KOKORO_FILES" in p for p, _ in found)
    bad = [(p, s, len(s)) for p, s in found if not HEX64.fullmatch(s)]
    assert not bad, bad


def test_manifest_pins_are_sha256():
    man = json.loads((Path(downloader.__file__).parent / "manifest.json").read_text("utf-8"))
    found = list(_shas(man))
    assert found
    bad = [(p, s, len(s)) for p, s in found if not HEX64.fullmatch(s)]
    assert not bad, bad
