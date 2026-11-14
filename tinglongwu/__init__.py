"""tinglongwu: fully local, offline real-time speech translator."""
from . import offline as _offline

# Must run before anything imports huggingface_hub / ctranslate2 / etc.
_offline.apply_env()
_offline.install_guard()

__version__ = "0.3.4"
