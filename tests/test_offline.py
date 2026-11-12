import os
import socket
import subprocess
import sys

import pytest

import tinglongwu
from tinglongwu.offline import NetworkBlocked


def test_env_set_on_import():
    for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK"):
        assert os.environ[k] == "1"


def test_env_set_before_other_imports():
    code = ("import os, sys; os.environ.pop('HF_HUB_OFFLINE', None); import tinglongwu; "
            "assert os.environ['HF_HUB_OFFLINE']=='1'; assert 'huggingface_hub' not in sys.modules")
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.parametrize("fn", [
    lambda: socket.create_connection(("8.8.8.8", 53), timeout=1),
    lambda: socket.socket().connect(("1.1.1.1", 80)),
    lambda: socket.getaddrinfo("example.com", 80),
    lambda: socket.gethostbyname("example.com"),
])
def test_guard_blocks_external(fn):
    with pytest.raises(NetworkBlocked):
        fn()


def test_guard_allows_loopback():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    c = socket.create_connection(("127.0.0.1", srv.getsockname()[1]), timeout=2)
    c.close()
    srv.close()
    assert tinglongwu.offline.is_loopback_host("::1") and tinglongwu.offline.is_loopback_host("localhost")
