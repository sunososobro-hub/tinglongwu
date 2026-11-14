"""Resumable, SHA256-checked file download (#673). Network code stays in tinglongwu_setup (never the runtime)."""
from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path


def _open_range(url: str, start: int, timeout: int = 60):
    h = {"User-Agent": "tlwu-setup/0.1"}
    if start:
        h["Range"] = f"bytes={start}-"
    return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout)


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fetch(url: str, dest: Path, sha256: str, opener=_open_range, on_bytes=None, chunk: int = 1 << 20) -> Path:
    """Download `url` to `dest`. A `.part` file left by an interrupted run is continued with an HTTP Range request
    (a server that ignores Range and sends 200 restarts the file). A finished file whose SHA256 is wrong is deleted
    and downloaded once more; a second mismatch raises ValueError. `opener(url, start)` returns a (status-aware)
    response: `.status`, `.read(n)`, context manager."""
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in (1, 2):
        start = part.stat().st_size if part.exists() else 0
        with opener(url, start) as r:
            if start and getattr(r, "status", 200) != 206:
                start = 0  # server ignored Range: start over
            with open(part, "ab" if start else "wb") as out:
                while True:
                    b = r.read(chunk)
                    if not b:
                        break
                    out.write(b)
                    start += len(b)
                    if on_bytes:
                        on_bytes(start)
        if _sha(part) == sha256:
            part.replace(dest)
            return dest
        part.unlink()  # corrupt: delete and retry once
    raise ValueError(f"SHA256 mismatch for {dest.name} after a retry")
