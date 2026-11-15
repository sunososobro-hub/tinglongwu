"""Runtime side of the speaker pack for packaged builds (no pip there): the sherpa-onnx wheels are unzipped into
models/speaker/pylib by `tlwu-setup --speaker-pylib`; this module only puts that folder on sys.path (and the DLL
folders on the Windows DLL search path). Offline, no network. Only called once speaker labels are switched on."""
from __future__ import annotations

import os
import sys
from pathlib import Path

PYLIB = ("speaker", "pylib")
_dll_handles: list = []


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
    if add is not None:
        for folder in sorted({p.parent for p in d.rglob("*.dll")}):
            try:
                _dll_handles.append(add(str(folder)))
            except OSError:
                pass
    return True
