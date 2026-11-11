import hashlib
import io

import pytest

from tinglongwu_setup import downloader as dl

DATA = b"fake-onnx-bytes"
NAME = "x_speaker.onnx"
FILES = {NAME: (len(DATA), hashlib.sha256(DATA).hexdigest())}


def _opener(data):
    return lambda url, timeout=60: io.BytesIO(data)


def test_good_hash_passes(tmp_path):
    dl.get_speaker_model({}, tmp_path, opener=_opener(DATA), files=FILES)
    assert (tmp_path / dl.SPEAKER_DIR / NAME).read_bytes() == DATA


def test_bad_hash_deleted_and_refused(tmp_path):
    bad = b"x" * len(DATA)
    with pytest.raises(SystemExit, match="MISMATCH"):
        dl.get_speaker_model({}, tmp_path, opener=_opener(bad), files=FILES)
    assert list((tmp_path / dl.SPEAKER_DIR).iterdir()) == []


def test_cnceleb_refused(tmp_path):
    called = []
    with pytest.raises(SystemExit, match="banned"):
        dl.get_speaker_model({}, tmp_path, opener=lambda *a: called.append(1), files={"CNCeleb_x.onnx": (1, "0" * 64)})
    assert not called


def test_default_pins_are_sane():
    for name, (size, sha) in {**dl.SPEAKER_FILES, **dl.SPEAKER_FALLBACK}.items():
        assert len(sha) == 64 and size > 0 and "cnceleb" not in name.lower()
