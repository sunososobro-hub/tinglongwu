"""resume.fetch retries a transient 429 / 5xx before giving up (#2051)."""
import hashlib
import io
import urllib.error

import pytest

from tinglongwu_setup import resume

DATA = b"model-bytes" * 10
SHA = hashlib.sha256(DATA).hexdigest()


class _Resp(io.BytesIO):
    status = 200
    headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _err(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


def _opener(errors):
    calls = []

    def op(url, start):
        calls.append(start)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return _Resp(DATA)

    op.calls = calls
    return op


def test_two_429_then_success(tmp_path):
    op, waits = _opener([_err(429), _err(429)]), []
    out = resume.fetch("http://x", tmp_path / "f", SHA, opener=op, sleep=waits.append)
    assert out.read_bytes() == DATA
    assert len(op.calls) == 3 and len(waits) == 2


def test_5xx_and_network_error_are_retried(tmp_path):
    op = _opener([_err(503), urllib.error.URLError("reset")])
    resume.fetch("http://x", tmp_path / "f", SHA, opener=op, sleep=lambda s: None)
    assert len(op.calls) == 3


def test_always_429_raises_after_four_attempts(tmp_path):
    op = _opener([_err(429)] * 10)
    with pytest.raises(urllib.error.HTTPError):
        resume.fetch("http://x", tmp_path / "f", SHA, opener=op, sleep=lambda s: None)
    assert len(op.calls) == 4


@pytest.mark.parametrize("code", [404, 403])
def test_client_errors_raise_on_first_attempt(tmp_path, code):
    op = _opener([_err(code)] * 10)
    with pytest.raises(urllib.error.HTTPError):
        resume.fetch("http://x", tmp_path / "f", SHA, opener=op, sleep=lambda s: None)
    assert len(op.calls) == 1
