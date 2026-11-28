"""Resumable, SHA256-checked file download (#673). Network code stays in tinglongwu_setup (never the runtime)."""
from __future__ import annotations

import hashlib
import time
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


_OPEN_ATTEMPTS = 4  # opener tries per start (#2051): a transient 429 / 5xx / network error is retried with backoff


def _transient(e: BaseException) -> bool:
    if isinstance(e, urllib.error.HTTPError):
        return e.code == 429 or 500 <= e.code < 600
    return isinstance(e, (urllib.error.URLError, ConnectionError, TimeoutError))


def fetch(url: str, dest: Path, sha256: str, opener=_open_range, on_bytes=None, chunk: int = 1 << 20,
          label: str | None = None, say=print, sleep=time.sleep) -> Path:
    """Download `url` to `dest`. A `.part` file left by an interrupted run is continued with an HTTP Range request
    (a server that ignores Range and sends 200 restarts the file). A transient HTTP 429 / 5xx or network error
    from `opener` is retried (4 tries, `sleep` backoff; #2051); 404 / 403 raise at once. A finished file whose SHA256 is wrong is deleted
    and downloaded once more; a second mismatch raises ValueError. `opener(url, start)` returns a (status-aware)
    response: `.status`, `.read(n)`, context manager. `label` (#1358): print `download <label>: N% (a/b MB)` every 5%
    so the GUI can show progress (`say` is the printer)."""
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    bad = 0  # checksum failures (one retry allowed); a 416 restart does not count against it
    for _ in range(4):
        if part.exists() and _sha(part) == sha256:  # finished earlier, stopped before the rename
            part.replace(dest)
            return dest
        start = part.stat().st_size if part.exists() else 0
        r = None
        for attempt in range(1, _OPEN_ATTEMPTS + 1):
            try:
                r = opener(url, start)
                break
            except urllib.error.HTTPError as e:
                if e.code == 416 and start:  # .part is bigger than / not a prefix of the file: start over
                    part.unlink()
                    break
                if not _transient(e) or attempt == _OPEN_ATTEMPTS:
                    raise
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                if attempt == _OPEN_ATTEMPTS:
                    raise
            sleep(2 ** attempt)  # 2, 4, 8 s; the .part prefix is kept
        if r is None:  # 416 restart
            continue
        with r:
            if start and getattr(r, "status", 200) != 206:
                start = 0  # server ignored Range: start over
            try:
                length = int((getattr(r, "headers", None) or {}).get("Content-Length") or 0)
            except (TypeError, ValueError):
                length = 0
            got, next_pct = 0, 5
            with open(part, "ab" if start else "wb") as out:
                while True:
                    b = r.read(chunk)
                    if not b:
                        break
                    out.write(b)
                    got += len(b)
                    if on_bytes:
                        on_bytes(start + got)
                    if label and length:
                        pct = min(100, (start + got) * 100 // (start + length))
                        if pct >= next_pct:
                            say(f"download {label}: {pct}% ({(start + got) >> 20}/{(start + length) >> 20} MB)")
                            next_pct = (pct // 5 + 1) * 5
            if length and got < length:  # early EOF: keep the .part prefix, the next call resumes from it
                raise ConnectionError(f"{dest.name}: connection ended after {start + got} of {start + length} bytes")
        if _sha(part) == sha256:
            part.replace(dest)
            return dest
        part.unlink()  # corrupt: delete and retry once
        bad += 1
        if bad == 2:
            break
    raise ValueError(f"SHA256 mismatch for {dest.name} after a retry")
