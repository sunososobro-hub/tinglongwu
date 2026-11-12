#!/usr/bin/env python3
"""Fail if tlwu (or any child process) opens a non-loopback network connection.

  python scripts/verify_offline.py --file talk.wav [-- extra tlwu args]
  python scripts/verify_offline.py --pid 1234 --seconds 60     # attach to a running tlwu
Polls the whole process tree with psutil every 50 ms. Exit 0 = clean, 1 = violation.
Polling can miss a very short-lived connection; combine with the firewall test in the README.
"""
from __future__ import annotations

import argparse
import ipaddress
import subprocess
import sys
import time

import psutil


def offending(proc: psutil.Process) -> list[str]:
    bad = []
    try:
        procs = [proc] + proc.children(recursive=True)
    except psutil.Error:
        return bad
    for p in procs:
        try:
            for c in p.net_connections(kind="inet"):
                if c.raddr and not ipaddress.ip_address(c.raddr.ip.split("%")[0]).is_loopback:
                    bad.append(f"pid {p.pid} -> {c.raddr.ip}:{c.raddr.port} ({c.status})")
        except (psutil.Error, ValueError):
            continue
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    ap.add_argument("--pid", type=int)
    ap.add_argument("--seconds", type=float, default=60)
    a, extra = ap.parse_known_args()
    extra = [x for x in extra if x != "--"]
    child = None
    if a.pid:
        proc, deadline = psutil.Process(a.pid), time.time() + a.seconds
    elif a.file:
        child = subprocess.Popen([sys.executable, "-m", "tinglongwu", "--file", a.file, *extra],
                                 stdout=subprocess.DEVNULL)
        proc, deadline = psutil.Process(child.pid), float("inf")
    else:
        ap.error("need --file or --pid")
    seen: set[str] = set()
    while time.time() < deadline and proc.is_running() and (child is None or child.poll() is None):
        seen.update(offending(proc))
        time.sleep(0.05)
    if child is not None and child.wait() != 0:
        print(f"WARNING: tlwu exited with code {child.returncode}")
    if seen:
        print("FAIL: non-loopback connections observed:\n  " + "\n  ".join(sorted(seen)))
        return 1
    print("PASS: no non-loopback connections observed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
