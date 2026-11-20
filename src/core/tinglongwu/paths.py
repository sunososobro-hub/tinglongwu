"""Central app paths (#1664). Stdlib only: `tinglongwu/__init__` imports offline first, and offline may import this.

APP_ROOT: the folder that holds the `tinglongwu/` package -- the repo root in a source checkout, the install dir
({app}, next to tlwu.exe) in the Nuitka standalone build. DATA_DIR: the package's bundled `data/` folder.
Source checkout: the package sits in <repo>/src/core/tinglongwu, so APP_ROOT is the repo root (holds pyproject.toml).
"""
from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


def _app_root(pkg: Path) -> Path:
    src_root = pkg.parent.parent.parent  # <repo>/src/core/tinglongwu -> <repo>
    if pkg.parent.parent.name == "src" and (src_root / "pyproject.toml").is_file():
        return src_root
    return pkg.parent  # frozen / flat layout: {app}/tinglongwu


APP_ROOT = _app_root(PACKAGE_DIR)
DATA_DIR = PACKAGE_DIR / "data"
