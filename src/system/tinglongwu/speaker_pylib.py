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
    place) exists and is not empty. Whether the pins match this build is setup's job: `tlwu-setup --speaker-pylib`
    re-extracts when the marker differs from its pins (the runtime package never imports tinglongwu_setup, #1070)."""
    try:
        got = (pylib_dir(mdir) / ".pins").read_text("utf-8")
    except OSError:
        return False
    return bool(got.strip())


def module_present(name: str) -> bool:
    """True when a top-level package/module `name` is on sys.path, found without importing it (no dynamic imports: the
    runtime package keeps static imports only, tests/test_no_network_code.py)."""
    for entry in sys.path:
        try:
            base = Path(entry or ".")
            if (base / name / "__init__.py").is_file() or any(base.glob(f"{name}*.pyd")) \
                    or any(base.glob(f"{name}*.so")) or (base / f"{name}.py").is_file():
                return True
        except (OSError, ValueError):
            continue
    return False
