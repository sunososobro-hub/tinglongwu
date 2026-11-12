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
    assert "m2m-1.2B" not in pins["models"]  # #284: M2M-100 is gone
    for a in pins["argos"].values():
        assert a["url"].startswith("https://") and a["files"]
        assert len(a.get("archive_sha256", "0" * 64)) == 64  # archive hash optional (files are always pinned)


def test_check_pinned_rejects_mismatch():
    dl.check_pinned({"a": "1"}, {"a": "1", "b": "2"}, "k")
    with pytest.raises(SystemExit, match="MISMATCH"):
        dl.check_pinned({"a": "1"}, {"a": "x"}, "k")


def test_guard_blocks_udp_sendto():
    import tinglongwu  # noqa: F401  (installs the guard)

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(OSError, match="blocked"):
            s.sendto(b"x", ("8.8.8.8", 53))
    finally:
        s.close()
