"""Offline enforcement: environment flags + an always-on network guard.

Importing :mod:`tinglongwu` calls :func:`apply_env` and :func:`install_guard` before any
HF / ctranslate2 code is imported. Only loopback and AF_UNIX connections pass.
"""
from __future__ import annotations

import ipaddress
import os
import socket

OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
    "ARGOS_CHECK_UPDATES": "0",
    "ARGOS_PACKAGE_INDEX": "",
}


class NetworkBlocked(OSError):
    """Raised when code inside tlwu tries to reach a non-loopback host."""


def apply_env() -> None:
    os.environ.update(OFFLINE_ENV)


def is_loopback_host(host) -> bool:
    if host in (None, "", "localhost", b"", b"localhost"):
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


def _check(host) -> None:
    if not is_loopback_host(host):
        raise NetworkBlocked(f"tlwu network guard: blocked connection to {host!r} (offline-only)")


_installed = False


def install_guard() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex
    orig_gai = socket.getaddrinfo
    orig_ghbn = socket.gethostbyname

    def _addr_host(addr):
        return addr[0] if isinstance(addr, tuple) else None  # str/bytes = AF_UNIX path

    def connect(self, addr):
        _check(_addr_host(addr))
        return orig_connect(self, addr)

    def connect_ex(self, addr):
        _check(_addr_host(addr))
        return orig_connect_ex(self, addr)

    def getaddrinfo(host, *a, **k):
        _check(host)
        return orig_gai(host, *a, **k)

    def gethostbyname(host):
        _check(host)
        return orig_ghbn(host)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo
    socket.gethostbyname = gethostbyname
