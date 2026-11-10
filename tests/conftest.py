import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture
def wav(tmp_path):
    import soundfile as sf

    p = tmp_path / "in.wav"
    t = np.arange(44100 * 3) / 44100
    sf.write(str(p), (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32"), 44100)
    return p


@pytest.fixture(autouse=True)
def _isolated_user_dirs(tmp_path, monkeypatch):
    """Network-guard logs / config go to a temp dir, never the real user dirs."""
    monkeypatch.setenv("TLWU_LOG_DIR", str(tmp_path / "_log"))
    monkeypatch.setenv("TLWU_CONFIG_DIR", str(tmp_path / "_cfg"))
    monkeypatch.delenv("TLWU_ALLOW_NET", raising=False)
