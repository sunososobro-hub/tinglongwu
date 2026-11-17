"""#1401: `tlwu-setup --whisper-cpp small` downloads models/whisper-cpp/ggml-small.bin from the committed pin (URL at the
pinned revision + SHA256), a tampered body is refused, and asr_vulkan._load_backend then finds the model."""
import hashlib
import io
import json

import pytest

from tinglongwu import asr_vulkan as av
from tinglongwu_setup import downloader as dl, resume

BODY = b"ggml-small model bytes" * 1000
REV = "0123456789abcdef0123456789abcdef01234567"


def _pins(body=BODY):
    return {"models": {"whisper-cpp-small": {"repo": dl.WHISPER_CPP_REPO, "revision": REV,
                                             "files": {"ggml-small.bin": hashlib.sha256(body).hexdigest()}}},
            "argos": {}}


class _Fn:
    def __init__(self, impl):
        self.impl, self.restype, self.argtypes = impl, None, None

    def __call__(self, *a):
        return self.impl(*a)


class _FakeLib:
    """The tlwu-whisper C API (see test_whisper_vulkan_783.FakeLib), enough for init/free."""

    def __init__(self, inits):
        self.tlwu_whisper_abi = _Fn(lambda: av.WHISPER_ABI)
        self.tlwu_whisper_init = _Fn(lambda path, gpu: (inits.append((path, gpu)), 1)[1])
        self.tlwu_whisper_free = _Fn(lambda c: None)
        self.tlwu_whisper_transcribe = _Fn(lambda *a: 0)


class _Resp(io.BytesIO):
    status, headers = 200, {}


@pytest.fixture
def net(monkeypatch):
    """Fake network: every URL resume.fetch opens is recorded and answered with `served` (no real request)."""
    state = {"urls": [], "served": BODY}
    real = resume.fetch

    def fetch(url, dest, sha, **kw):
        kw.pop("opener", None)
        return real(url, dest, sha, opener=lambda u, start: (state["urls"].append(u), _Resp(state["served"]))[1], **kw)

    monkeypatch.setattr(resume, "fetch", fetch)
    return state


def test_whisper_cpp_model_installs_from_the_pinned_url_and_asr_vulkan_finds_it(tmp_path, monkeypatch, net):
    monkeypatch.setattr(dl, "load_pins", _pins)
    models = tmp_path / "models"
    assert dl.main(["--whisper-cpp", "small", "--models-dir", str(models)]) == 0
    model = models / av.MODEL_DIR / "ggml-small.bin"
    assert model.read_bytes() == BODY
    assert net["urls"] == [f"https://huggingface.co/ggerganov/whisper.cpp/resolve/{REV}/ggml-small.bin"]
    man = json.loads((models / "manifest.json").read_text("utf-8"))
    assert man["whisper-cpp-small"]["files"] == {"ggml-small.bin": hashlib.sha256(BODY).hexdigest()}
    assert dl.main(["--verify", "--models-dir", str(models)]) == 0  # offline re-check of the recorded pin
    # second run: already installed and verified, no new download
    assert dl.main(["--whisper-cpp", "small", "--models-dir", str(models)]) == 0 and len(net["urls"]) == 1

    pack = tmp_path / "vulkan-pack"
    (pack / av.WHISPER_DIR).mkdir(parents=True)
    (pack / av.WHISPER_DIR / av.WHISPER_LIB).write_bytes(b"lib")
    inits = []
    b = av._load_backend(pack, models, "small", load=lambda p: _FakeLib(inits))
    assert isinstance(b, av.WhisperCppBackend) and inits == [(str(model).encode("utf-8"), 1)]


def test_whisper_cpp_model_tampered_body_fails_the_sha256_check(tmp_path, monkeypatch, net):
    monkeypatch.setattr(dl, "load_pins", _pins)
    net["served"] = BODY[:-1] + b"X"
    models = tmp_path / "models"
    with pytest.raises(ValueError):
        dl.main(["--whisper-cpp", "small", "--models-dir", str(models)])
    assert not (models / av.MODEL_DIR / "ggml-small.bin").exists()
    pack = tmp_path / "vulkan-pack"
    (pack / av.WHISPER_DIR).mkdir(parents=True)
    (pack / av.WHISPER_DIR / av.WHISPER_LIB).write_bytes(b"lib")
    with pytest.raises(FileNotFoundError):  # so the Vulkan ASR falls back to the CPU, never loads a tampered model
        av._load_backend(pack, models, "small", load=lambda p: None)


def test_whisper_cpp_model_on_disk_tampered_later_fails_verify(tmp_path, monkeypatch, net):
    monkeypatch.setattr(dl, "load_pins", _pins)
    models = tmp_path / "models"
    dl.main(["--whisper-cpp", "small", "--models-dir", str(models)])
    (models / av.MODEL_DIR / "ggml-small.bin").write_bytes(b"evil")
    with pytest.raises(SystemExit, match="MISMATCH"):
        dl.main(["--verify", "--models-dir", str(models)])


def test_whisper_cpp_without_a_committed_pin_refuses_to_download(tmp_path, monkeypatch, net):
    monkeypatch.setattr(dl, "load_pins", lambda: {"models": {}, "argos": {}})
    with pytest.raises(SystemExit, match="make_model_pins"):
        dl.main(["--whisper-cpp", "small", "--models-dir", str(tmp_path)])
    assert net["urls"] == []


def test_whisper_cpp_model_dir_matches_asr_vulkan():
    assert dl.WHISPER_CPP_DIR == av.MODEL_DIR
    assert dl.whisper_cpp_file("small") == "ggml-small.bin"
