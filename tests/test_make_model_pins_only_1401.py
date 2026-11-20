"""#1401: `make_model_pins.py --only whisper-cpp-` adds the whisper.cpp pins and leaves every other pin untouched."""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

from tinglongwu_setup import downloader as dl

ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location("make_model_pins", ROOT / "scripts" / "make_model_pins.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Api:
    """Fake HfApi: every ggml-*.bin of the whisper.cpp repo is an LFS file whose SHA256 the Hub reports."""

    def __init__(self):
        self.calls = []

    def model_info(self, repo, revision=None, files_metadata=False):
        self.calls.append(repo)
        assert repo == dl.WHISPER_CPP_REPO, repo
        sib = [types.SimpleNamespace(rfilename=dl.whisper_cpp_file(s), lfs=types.SimpleNamespace(sha256=f"{i:064x}"))
               for i, s in enumerate(dl.WHISPER_CPP_SIZES, 1)]
        sib.append(types.SimpleNamespace(rfilename="ggml-small.en.bin", lfs=types.SimpleNamespace(sha256="f" * 64)))
        return types.SimpleNamespace(sha="c0ffee" * 6 + "abcd", siblings=sib)


@pytest.fixture
def pins_file(tmp_path, monkeypatch):
    f = tmp_path / "manifest.json"
    f.write_text((ROOT / "src" / "system" / "tinglongwu_setup" / "manifest.json").read_text("utf-8"), "utf-8", newline="\n")
    monkeypatch.setattr(dl, "PINS_FILE", f)
    return f


@pytest.fixture
def fake_hub(monkeypatch):
    # main() pops HF_HUB_OFFLINE from the real environment; register it so monkeypatch restores it afterwards
    # (other tests, e.g. test_offline.py, rely on it).
    import os

    monkeypatch.setenv("HF_HUB_OFFLINE", os.environ.get("HF_HUB_OFFLINE", "1"))
    api = _Api()
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(HfApi=lambda: api))
    return api


def test_only_whisper_cpp_adds_pins_and_keeps_every_other_pin(pins_file, fake_hub):
    before = json.loads(pins_file.read_text("utf-8"))
    _load_script().main(["--only", "whisper-cpp-"])
    after = json.loads(pins_file.read_text("utf-8"))
    assert after["argos"] == before["argos"]
    for k, v in before["models"].items():
        if not k.startswith("whisper-cpp-"):
            assert after["models"][k] == v, k
    assert set(fake_hub.calls) == {dl.WHISPER_CPP_REPO}  # nothing else was refreshed
    for i, s in enumerate(dl.WHISPER_CPP_SIZES, 1):
        pin = after["models"][f"whisper-cpp-{s}"]
        assert pin["repo"] == dl.WHISPER_CPP_REPO and pin["revision"] == "c0ffee" * 6 + "abcd"
        assert pin["files"] == {dl.whisper_cpp_file(s): f"{i:064x}"}  # only the one file, not ggml-small.en.bin


def test_only_whisper_cpp_pin_is_what_get_whisper_cpp_uses(pins_file, fake_hub):
    _load_script().main(["--only", "whisper-cpp-small"])
    pin = dl.load_pins()["models"]["whisper-cpp-small"]
    assert list(pin["files"]) == [dl.whisper_cpp_file("small")]


def test_only_with_unknown_prefix_exits_and_writes_nothing(pins_file, fake_hub):
    text = pins_file.read_text("utf-8")
    with pytest.raises(SystemExit, match="matches no"):
        _load_script().main(["--only", "nope-"])
    assert pins_file.read_text("utf-8") == text


def test_only_with_empty_prefix_exits_instead_of_a_full_refresh(pins_file, fake_hub):
    text = pins_file.read_text("utf-8")
    with pytest.raises(SystemExit):
        _load_script().main(["--only", ""])
    assert pins_file.read_text("utf-8") == text and fake_hub.calls == []


@pytest.mark.parametrize("size", dl.WHISPER_CPP_SIZES)
def test_committed_manifest_pins_every_whisper_cpp_size(size):
    pin = dl.load_pins()["models"][f"whisper-cpp-{size}"]
    assert pin["repo"] == dl.WHISPER_CPP_REPO and len(pin["revision"]) == 40
    (name, sha), = pin["files"].items()
    assert name == dl.whisper_cpp_file(size) and len(sha) == 64 and int(sha, 16) >= 0
