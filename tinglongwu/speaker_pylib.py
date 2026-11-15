"""Runtime side of the speaker pack for packaged builds (no pip there): the sherpa-onnx wheels are unzipped into
models/speaker/pylib by `tlwu-setup --speaker-pylib`; this module only puts that folder on sys.path (and the DLL
folders on the Windows DLL search path). Offline, no network. Only called once speaker labels are switched on."""
from __future__ import annotations

import os
import sys
from pathlib import Path

PYLIB = ("speaker", "pylib")
_dll_handles: dict = {}  # pylib dir -> handles (DLL folders are added once)


def pylib_dir(mdir) -> Path:
    return Path(mdir).joinpath(*PYLIB)


def activate(mdir) -> bool:
    """True when the pylib folder exists and is on sys.path (idempotent). A missing folder changes nothing."""
    d = pylib_dir(mdir)
    if not d.is_dir():
        return False
    s = str(d)
    if s not in sys.path:
        sys.path.insert(0, s)
    add = getattr(os, "add_dll_directory", None)
    if add is not None and s not in _dll_handles:
        handles = _dll_handles[s] = []
        for folder in sorted({p.parent for p in d.rglob("*.dll")}):
            try:
                handles.append(add(str(folder)))
            except OSError:
                pass
    return True


def pylib_valid(mdir) -> bool:
    """The pylib folder was completely extracted: the .pins marker (written last, then the folder is renamed into
    place) exists and matches the pins of this build when those are known."""
    try:
        got = (pylib_dir(mdir) / ".pins").read_text("utf-8")
    except OSError:
        return False
    if not got.strip():
        return False
    try:
        from tinglongwu_setup.downloader import expected_speaker_pins

        want = expected_speaker_pins()
    except Exception:  # noqa: BLE001 - setup package absent: the marker alone decides
        return True
    return want is None or got == want
