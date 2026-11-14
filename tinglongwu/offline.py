"""Offline enforcement: environment flags + an always-on network guard.

Importing :mod:`tinglongwu` calls :func:`apply_env` and :func:`install_guard` before any
HF / ctranslate2 code is imported. Only loopback and AF_UNIX connections pass. The TTS worker subprocesses
(tts_worker.py) load this module too. Principle: the network is for install/setup (tlwu-setup, pip) and update (git)
only, none of which import this package. Escape hatch: env TLWU_ALLOW_NET=1 disables the guard.

Every blocked attempt prints one localized line (tinglongwu.i18n) and appends one JSON line to
<log dir>/netguard.jsonl (tinglongwu.userdirs): time, host, ip, port, mode, package guessed from the stack, trimmed
stack (module:function:line only, no source text, no user content), tlwu version/git sha, OS. Nothing is ever sent;
tinglongwu.netreport lets the user choose to open a pre-filled GitHub issue.

Limits (be honest about them): this is a Python-level guard. It patches socket.connect/connect_ex/sendto/sendmsg and
name resolution (getaddrinfo, gethostbyname[_ex], gethostbyaddr, getnameinfo), so it does NOT cover native code that opens sockets itself (ctranslate2, onnxruntime,
llama.cpp, other C extensions) or subprocesses. The real proof is an OS firewall test (README, "Verify").
"""
from __future__ import annotations

from . import diag as _diag

import ipaddress
import json
import os
import platform
import socket
import subprocess
import sys
import time
from pathlib import Path

OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
    "ARGOS_CHECK_UPDATES": "0",
    "ARGOS_PACKAGE_INDEX": "",
    "GRADIO_ANALYTICS_ENABLED": "False",
    "ANONYMIZED_TELEMETRY": "False",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "NO_ALBUMENTATIONS_UPDATE": "1",
}


LOG_NAME = "netguard.jsonl"
_mode = os.environ.get("TLWU_MODE", "unknown")


def set_mode(mode: str) -> None:
    """Entry points call this (listen / speak / meeting / gui / worker); subprocesses inherit it via env."""
    global _mode
    _mode = mode
    os.environ["TLWU_MODE"] = mode


def get_mode() -> str:
    return _mode


class NetworkBlocked(OSError):
    """Raised when code inside tlwu tries to reach a non-loopback host."""


def apply_env() -> None:
    os.environ.update(OFFLINE_ENV)


def allow_net() -> bool:
    return os.environ.get("TLWU_ALLOW_NET", "") == "1"


def is_loopback_host(host) -> bool:
    if host in (None, "", "localhost", b"", b"localhost"):
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


_sha: str | None = None
_seen: set = set()


def _version_info() -> tuple[str, str]:
    global _sha
    try:
        from . import __version__ as ver
    except Exception:  # noqa: BLE001
        ver = "?"
    if _sha is None:
        _sha = ""
        try:
            root = Path(__file__).resolve().parent.parent
            if (root / ".git").exists():
                _sha = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True,
                                      text=True, timeout=2).stdout.strip()
        except Exception:  # noqa: BLE001
            _diag.swallowed(__name__)
            pass
    return ver, _sha


def _stack() -> tuple[str, list[str]]:
    """(top-level non-stdlib package nearest the call, trimmed frames outermost first). No source lines."""
    std = getattr(sys, "stdlib_module_names", frozenset())
    frames, pkg = [], ""
    f = sys._getframe(2)
    while f is not None:
        mod = f.f_globals.get("__name__", "?") or "?"
        if mod != __name__:
            frames.append(f"{mod}:{f.f_code.co_name}:{f.f_lineno}")
            top = mod.split(".")[0]
            if not pkg and top not in std and top not in ("__main__", "?"):
                pkg = top
        f = f.f_back
    return pkg or "unknown", list(reversed(frames))[-25:]


def _emit(host, port) -> None:
    from . import i18n, userdirs

    pkg, frames = _stack()
    key = (str(host), port, pkg)
    if key in _seen:
        return
    _seen.add(key)
    ver, sha = _version_info()
    ip = host if isinstance(host, str) and _is_ip(host) else None
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "host": str(host), "ip": ip, "port": port, "mode": _mode,
           "package": pkg, "stack": frames, "version": ver, "sha": sha, "os": platform.platform(),
           "reported": False}
    try:
        d = userdirs.log_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / LOG_NAME, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        _diag.swallowed(__name__)
        pass
    msg = i18n.t("guard_blocked", host=host, port=port if port is not None else "?", pkg=pkg)
    try:
        print(msg, file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001  (legacy console encodings / no stderr)
        try:
            print(msg.encode("ascii", "replace").decode(), file=sys.stderr, flush=True)
        except Exception:  # noqa: BLE001
            _diag.swallowed(__name__)
            pass


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%")[0])
        return True
    except ValueError:
        return False


def _check(host, port=None) -> None:
    if allow_net() or is_loopback_host(host):
        return
    try:
        _emit(host, port if isinstance(port, int) else None)
    except Exception:  # noqa: BLE001  (never let reporting mask the block)
        _diag.swallowed(__name__)
        pass
    raise NetworkBlocked(f"tlwu network guard: blocked connection to {host!r} (offline-only)")


_installed = False


def install_guard() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex
    orig_sendto = socket.socket.sendto
    orig_gai = socket.getaddrinfo
    orig_ghbn = socket.gethostbyname
    orig_cc = socket.create_connection
    orig_ghbn_ex = socket.gethostbyname_ex
    orig_ghba = socket.gethostbyaddr
    orig_gni = socket.getnameinfo
    orig_sendmsg = getattr(socket.socket, "sendmsg", None)

    def _addr(addr):
        if isinstance(addr, tuple) and addr:
            return addr[0], (addr[1] if len(addr) > 1 else None)
        return None, None  # str/bytes = AF_UNIX path

    def connect(self, addr):
        _check(*_addr(addr))
        return orig_connect(self, addr)

    def connect_ex(self, addr):
        _check(*_addr(addr))
        return orig_connect_ex(self, addr)

    def sendto(self, data, *rest):  # UDP: sendto(data, addr) / sendto(data, flags, addr)
        _check(*_addr(rest[-1]) if rest else (None, None))
        return orig_sendto(self, data, *rest)

    def getaddrinfo(host, port=None, *a, **k):  # asyncio's loop.getaddrinfo ends up here too
        _check(host, port)
        return orig_gai(host, port, *a, **k)

    def gethostbyname(host):
        _check(host)
        return orig_ghbn(host)

    def create_connection(address, *a, **k):
        _check(*_addr(address))
        return orig_cc(address, *a, **k)

    def gethostbyname_ex(host):
        _check(host)
        return orig_ghbn_ex(host)

    def gethostbyaddr(host):
        _check(host)
        return orig_ghba(host)

    def getnameinfo(sockaddr, flags):  # reverse DNS: sends the address to a resolver
        _check(*_addr(sockaddr))
        return orig_gni(sockaddr, flags)

    def sendmsg(self, buffers, *rest):  # sendmsg(buffers[, ancdata[, flags[, address]]])
        addr = rest[2] if len(rest) > 2 else None
        if addr is not None:
            _check(*_addr(addr))
        return orig_sendmsg(self, buffers, *rest)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto
    socket.getaddrinfo = getaddrinfo
    socket.gethostbyname = gethostbyname
    socket.create_connection = create_connection
    socket.gethostbyname_ex = gethostbyname_ex
    socket.gethostbyaddr = gethostbyaddr
    socket.getnameinfo = getnameinfo
    if orig_sendmsg is not None:  # not available on Windows
        socket.socket.sendmsg = sendmsg
