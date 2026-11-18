"""#1499: get_whisper downloads pinned files from /resolve/<rev>/ and never touches the HF /api/ tree listing."""
import hashlib
import io
import urllib.error

import pytest

from tinglongwu_setup import downloader as dl
from tinglongwu_setup import resume


class _Resp(io.BytesIO):
    status = 200
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _setup(monkeypatch, tmp_path, opener):
    files = {"config.json": b"{}", "model.bin": b"weights", "tokenizer.json": b"tok", "vocabulary.txt": b"voc"}
    pf = {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}
    monkeypatch.setattr(dl, "load_pins", lambda: {"models": {"whisper-small": {
        "repo": "Systran/faster-whisper-small", "revision": "abc123", "files": pf}}, "argos": {}})
    monkeypatch.setattr(resume, "_open_range", opener)
    return files


def test_get_whisper_never_requests_api(monkeypatch, tmp_path):
    urls = []
    files = {}

    def opener(url, start=0, timeout=60):
        urls.append(url)
        return _Resp(files[url.rsplit("/", 1)[1]])

    files.update(_setup(monkeypatch, tmp_path, opener))
    man = {}
    dl.get_whisper(man, tmp_path, "small")
    assert urls and all("/api/" not in u for u in urls)
    assert all(u.startswith("https://huggingface.co/Systran/faster-whisper-small/resolve/abc123/") for u in urls)
    assert (tmp_path / "whisper-small" / "model.bin").read_bytes() == b"weights"
    assert man["whisper-small"]["revision"] == "abc123"


def test_get_whisper_failure_names_host_and_manual_doc(monkeypatch, tmp_path):
    def opener(url, start=0, timeout=60):
        raise urllib.error.URLError("blocked by proxy")

    _setup(monkeypatch, tmp_path, opener)
    with pytest.raises(SystemExit) as e:
        dl.get_whisper({}, tmp_path, "small")
    msg = str(e.value)
    assert "huggingface.co" in msg and "resolve/abc123" in msg and "blocked by proxy" in msg
    assert dl.MANUAL_INSTALL_DOC in msg
