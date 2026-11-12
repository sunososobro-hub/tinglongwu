"""#422: every committed SHA256 pin is 64 lowercase hex chars (a lost digit made every fresh Kokoro download fail).
Pins are picked by where they sit (not by what they look like), so a malformed one cannot slip past the check."""
from __future__ import annotations

import json
import re
from pathlib import Path

from tinglongwu_setup import downloader

HEX64 = re.compile(r"[0-9a-f]{64}")


def _downloader_pins():
    """(size, sha256) tables (*_FILES, *_FALLBACK) and {name: sha256} tables (*_PINS)."""
    for n, t in vars(downloader).items():
        if not (n.isupper() and isinstance(t, dict)):
            continue
        for k, v in t.items():
            if isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], int):
                yield f"{n}[{k}]", v[1]
            elif n.endswith("_PINS"):
                yield f"{n}[{k}]", v


def _manifest_pins(man):
    """Every value of a "files" table, and every "archive_sha256"."""
    for sec, entries in man.items():
        for key, e in entries.items():
            for f, h in e.get("files", {}).items():
                yield f"{sec}/{key}/files/{f}", h
            if "archive_sha256" in e:
                yield f"{sec}/{key}/archive_sha256", e["archive_sha256"]


def test_downloader_constant_pins_are_sha256():
    found = list(_downloader_pins())
    assert any(p.startswith("KOKORO_FILES") for p, _ in found)
    bad = [(p, s) for p, s in found if not (isinstance(s, str) and HEX64.fullmatch(s))]
    assert not bad, bad


def test_manifest_pins_are_sha256():
    man = json.loads((Path(downloader.__file__).parent / "manifest.json").read_text("utf-8"))
    found = list(_manifest_pins(man))
    assert found
    bad = [(p, s) for p, s in found if not (isinstance(s, str) and HEX64.fullmatch(s))]
    assert not bad, bad
