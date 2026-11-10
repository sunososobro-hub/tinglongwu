import json
import socket

import pytest

from tinglongwu_setup import downloader as dl


def test_committed_pins_complete():
    pins = json.loads(dl.PINS_FILE.read_text("utf-8"))
    for size in ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"):
        p = pins["models"][f"whisper-{size}"]
        assert p["repo"] == dl.whisper_repo(size) and len(p["revision"]) == 40
        assert len(p["files"]["model.bin"]) == 64
    for name, (repo, fname) in dl.QWEN_GGUF.items():
        assert pins["models"][name]["repo"] == repo and fname in pins["models"][name]["files"]
    m = pins["models"]["m2m-1.2B"]
    assert "model.safetensors" in m["files"] and "pytorch_model.bin" not in m["files"]
    for a in pins["argos"].values():
        assert a["url"].startswith("https://") and a["files"]
        assert len(a.get("archive_sha256", "0" * 64)) == 64  # archive hash optional (files are always pinned)


def test_check_pinned_rejects_mismatch():
    dl.check_pinned({"a": "1"}, {"a": "1", "b": "2"}, "k")
    with pytest.raises(SystemExit, match="MISMATCH"):
        dl.check_pinned({"a": "1"}, {"a": "x"}, "k")


def test_m2m_refuses_without_safetensors_pin(monkeypatch, tmp_path):
    import subprocess

    class R:
        returncode = 0

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    monkeypatch.setattr(dl, "resolve_hf", lambda man, key, repo: (repo, "x" * 40, None))
    with pytest.raises(SystemExit, match="safetensors"):
        dl.get_m2m({}, tmp_path)


def test_guard_blocks_udp_sendto():
    import tinglongwu  # noqa: F401  (installs the guard)

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(OSError, match="blocked"):
            s.sendto(b"x", ("8.8.8.8", 53))
    finally:
        s.close()
