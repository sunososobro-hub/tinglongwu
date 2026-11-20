"""VB-CABLE download + elevated install for speak mode (#804, part 1 of #758). Networked, so it lives here and runs
only as a child process of the GUI (tinglongwu/vbcable.py decides when; the runtime never opens a socket).

  --download DIR --url URL --sha256 HEX
        download URL into DIR (resumable, resume.fetch); prints "READY <path>" when its SHA-256 equals HEX.
        Exit 1 with "FAIL <why>" otherwise (a mismatch deletes the file). An empty HEX or TLWU_OFFLINE=1 downloads
        nothing (exit 2).
  --install ZIP --sha256 HEX
        read ZIP once, check those bytes against HEX again, extract the same bytes into a fresh private temp folder
        (tempfile.mkdtemp, one per install) and start VBCABLE_Setup_x64.exe (32-bit Windows:
        VBCABLE_Setup.exe) elevated (UAC prompt) with "-i -h" (install, no setup window); waits for it. Exit code of
        the setup; 1 on a mismatch, a missing setup or a cancelled UAC prompt. Nothing runs on a mismatch.

Run: `python -m tinglongwu_setup.vbcable ...`. Does not import tinglongwu (that would install the network guard).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import platform
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from . import resume

SETUP_ARGS = ("-i", "-h")


def download(dest_dir: Path, url: str, sha256: str, fetch=None) -> Path:
    name = url.rstrip("/").rsplit("/", 1)[-1] or "vbcable.zip"
    return (fetch or resume.fetch)(url, Path(dest_dir) / name, sha256.lower())


def setup_exe_name(machine: str | None = None) -> str:
    m = (machine or platform.machine() or "").lower()
    return "VBCABLE_Setup_x64.exe" if m in ("amd64", "x86_64", "arm64", "aarch64") else "VBCABLE_Setup.exe"


def extract(src, out: Path | None = None) -> Path:
    """Extract `src` (a zip path, or the zip's bytes) into `out` (default: next to the zip). No absolute paths or
    ".." escapes."""
    if out is None:
        out = Path(src).with_suffix("")
    with zipfile.ZipFile(io.BytesIO(src) if isinstance(src, bytes) else src) as z:
        for info in z.infolist():
            target = (out / info.filename).resolve()
            if not target.is_relative_to(out.resolve()):
                raise ValueError(f"unsafe path in zip: {info.filename}")
        z.extractall(out)
    return out


def elevate_cmd(exe: Path) -> list[str]:
    args = ",".join(f"'{a}'" for a in SETUP_ARGS)
    path = str(exe).replace("'", "''")  # PowerShell single-quoted string
    ps = (f"$p = Start-Process -FilePath '{path}' -ArgumentList {args} -Verb RunAs -Wait -PassThru; "
          "exit $p.ExitCode")
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps]


def install(zip_path: Path, sha256: str, run=None, machine: str | None = None) -> int:
    """#804 review: the zip is read once; the bytes that pass the SHA-256 check are the bytes extracted, into a new
    tempfile.mkdtemp() folder per install. So a zip (or a stale folder from an earlier run) that is swapped
    between the check and the elevated start can never put other code in front of the UAC prompt."""
    try:
        data = Path(zip_path).read_bytes()
    except OSError as e:
        print(f"FAIL {type(e).__name__}: {e}"[:200])
        return 1
    if not sha256 or hashlib.sha256(data).hexdigest() != sha256.lower():
        print("FAIL SHA-256 mismatch: nothing was run")
        return 1
    folder = extract(data, Path(tempfile.mkdtemp(prefix="tlwu-vbcable-")))
    exe = next(iter(folder.rglob(setup_exe_name(machine))), None)
    if exe is None:
        print(f"FAIL {setup_exe_name(machine)} not in the package")
        return 1
    try:
        rc = (run or subprocess.run)(elevate_cmd(exe)).returncode
    except Exception as e:  # noqa: BLE001
        print(f"FAIL {type(e).__name__}: {e}"[:200])
        return 1
    print("INSTALLED" if rc == 0 else f"FAIL setup exit code {rc} (UAC cancelled?)")
    return 0 if rc == 0 else 1


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    p = argparse.ArgumentParser(prog="python -m tinglongwu_setup.vbcable")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--download", metavar="DIR")
    g.add_argument("--install", metavar="ZIP")
    p.add_argument("--url", default="")
    p.add_argument("--sha256", default="")
    a = p.parse_args(argv)
    if a.install:
        return install(Path(a.install), a.sha256)
    if os.environ.get("TLWU_OFFLINE") == "1" or not a.sha256 or not a.url:
        print("FAIL no download (offline, or no SHA-256 pin)")
        return 2
    try:
        path = download(Path(a.download), a.url, a.sha256)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL {type(e).__name__}: {e}"[:200])
        return 1
    print(f"READY {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
