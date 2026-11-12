"""#179: scripts/linux_kokoro.sh creates the Kokoro venv and fetches SHA256-pinned model files (run for real, local file:// source)."""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

SH = Path(__file__).resolve().parent.parent / "scripts" / "linux_kokoro.sh"


def _run(tmp_path, onnx_sha, voices_sha):
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "kokoro-v1.0.int8.onnx").write_bytes(b"onnx-bytes")
    (src / "voices-v1.0.bin").write_bytes(b"voices")
    env = dict(os.environ, HOME=str(tmp_path / "home"), PYTHON=sys.executable, TLWU_KOKORO_SKIP_PIP="1",
               KOKORO_BASE=src.as_uri(), KOKORO_ONNX_SIZE="10", KOKORO_ONNX_SHA256=onnx_sha,
               KOKORO_VOICES_SIZE="6", KOKORO_VOICES_SHA256=voices_sha)
    (tmp_path / "home").mkdir(exist_ok=True)
    return subprocess.run(["bash", str(SH)], env=env, capture_output=True, text=True)


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def test_installs_venv_and_verified_models(tmp_path):
    r = _run(tmp_path, _sha(b"onnx-bytes"), _sha(b"voices"))
    assert r.returncode == 0, r.stderr
    home = tmp_path / "home"
    assert (home / "tts-kokoro" / "bin" / "python").exists()
    assert (home / "models/kokoro/kokoro-v1.0.int8.onnx").read_bytes() == b"onnx-bytes"
    assert (home / "models/kokoro/voices-v1.0.bin").exists()
    assert "kokoro-v1.0.int8.onnx" in (home / "models/kokoro/SHA256SUMS").read_text()
    assert _run(tmp_path, _sha(b"onnx-bytes"), _sha(b"voices")).returncode == 0  # re-run is safe


def test_sha_mismatch_aborts_and_deletes(tmp_path):
    r = _run(tmp_path, "0" * 64, _sha(b"voices"))
    assert r.returncode != 0
    assert "SHA256 mismatch" in r.stderr
    d = tmp_path / "home/models/kokoro"
    assert not (d / "kokoro-v1.0.int8.onnx").exists()
    assert not (d / "kokoro-v1.0.int8.onnx.part").exists()

