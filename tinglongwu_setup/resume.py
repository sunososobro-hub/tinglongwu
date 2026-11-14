"""Resumable, SHA256-checked file download (#673). Network code stays in tinglongwu_setup (never the runtime)."""
from __future__ import annotations

import hashlib
import urllib.error
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
        if part.exists() and _sha(part) == sha256:  # finished earlier, stopped before the rename
            part.replace(dest)
            return dest
        start = part.stat().st_size if part.exists() else 0
        try:
            r = opener(url, start)
        except urllib.error.HTTPError as e:
            if e.code == 416 and start:  # .part is bigger than / not a prefix of the file: start over
                part.unlink()
                continue
            raise
        with r:
            if start and getattr(r, "status", 200) != 206:
                start = 0  # server ignored Range: start over
            try:
                length = int((getattr(r, "headers", None) or {}).get("Content-Length") or 0)
            except (TypeError, ValueError):
                length = 0
            got = 0
            with open(part, "ab" if start else "wb") as out:
                while True:
                    b = r.read(chunk)
                    if not b:
                        break
                    out.write(b)
                    got += len(b)
                    if on_bytes:
                        on_bytes(start + got)
            if length and got < length:  # early EOF: keep the .part prefix, the next call resumes from it
                raise ConnectionError(f"{dest.name}: connection ended after {start + got} of {start + length} bytes")
        if _sha(part) == sha256:
            part.replace(dest)
            return dest
        part.unlink()  # corrupt: delete and retry once
    raise ValueError(f"SHA256 mismatch for {dest.name} after a retry")
