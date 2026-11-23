"""tinglongwu: fully local, offline real-time speech translator."""
from pathlib import Path as _Path

# src/ layer layout (#1664): this package's modules live in src/{core,ui,flow,system}/tinglongwu/.
# Extend __path__ to the sibling layer dirs that exist; in the frozen build they are merged into one folder.
_layers = _Path(__file__).resolve().parent.parent.parent
for _layer in ("ui", "flow", "system"):
    _d = _layers / _layer / "tinglongwu"
    if _d.is_dir() and str(_d) not in __path__:
        __path__.append(str(_d))
del _layers, _layer, _d, _Path

from . import offline as _offline

# Must run before anything imports huggingface_hub / ctranslate2 / etc.
_offline.apply_env()
_offline.install_guard()

__version__ = "0.5.4"
