"""Local model locations. Models are never downloaded at runtime."""
from __future__ import annotations

import os
from pathlib import Path


class ModelNotFound(RuntimeError):
    pass


def models_dir(arg: str | None = None) -> Path:
    if arg:
        return Path(arg)
    if os.environ.get("TLWU_MODELS"):
        return Path(os.environ["TLWU_MODELS"])
    return Path(__file__).resolve().parent.parent / "models"


def require(path: Path, what: str, hint: str) -> Path:
    if not path.exists():
        raise ModelNotFound(
            f"{what} not found at {path}.\n"
            f"tlwu never downloads models at runtime. Run once (with network):\n"
            f"  tlwu-setup {hint}"
        )
    return path
