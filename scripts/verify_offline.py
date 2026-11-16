#!/usr/bin/env python3
"""Fail if tlwu (or any child process) opens a non-loopback network connection.

  python scripts/verify_offline.py --file talk.wav [-- extra tlwu args]
  python scripts/verify_offline.py --mic --seconds 60            # live microphone (room sound), stop after 60 s
  python scripts/verify_offline.py --pid 1234 --seconds 60     # attach to a running tlwu
  --stop-file PATH also ends the watch as soon as PATH exists (#1194: hw-tests stops a --mic run after playback).
Polls the whole process tree with psutil every 50 ms. Exit 0 = clean, 1 = violation.
Polling can miss a very short-lived connection; combine with the firewall test in the README.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
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


def build_cmd(file: str | None, mic: bool, extra: list[str]) -> list[str]:
    """Command line for the tlwu child: --file <wav> or --mic (live microphone)."""
    return [sys.executable, "-m", "tinglongwu", *(["--mic"] if mic else ["--file", file]), *extra]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    ap.add_argument("--mic", action="store_true", help="run tlwu --mic and watch it for --seconds")
    ap.add_argument("--pid", type=int)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--stop-file", default=None, help="end the watch as soon as this file exists")
    a, extra = ap.parse_known_args()
    extra = [x for x in extra if x != "--"]
    child = None
    if a.pid:
        proc, deadline = psutil.Process(a.pid), time.time() + a.seconds
    elif a.file or a.mic:
        child = subprocess.Popen(build_cmd(a.file, a.mic, extra), stdout=subprocess.DEVNULL)
        proc = psutil.Process(child.pid)
        deadline = time.time() + a.seconds if a.mic else float("inf")
    else:
        ap.error("need --file, --mic or --pid")
    seen: set[str] = set()

    def stop_asked() -> bool:
        return bool(a.stop_file) and os.path.exists(a.stop_file)

    while time.time() < deadline and not stop_asked() and proc.is_running() and (child is None or child.poll() is None):
        seen.update(offending(proc))
        time.sleep(0.05)
    early_exit = False
    if child is not None and a.mic:
        if child.poll() is None:
            child.terminate()  # deadline reached: the verifier stops it
        else:
            early_exit = True  # tlwu died before the watch window ended
    if child is not None and not a.mic and child.wait() != 0:
        print(f"WARNING: tlwu exited with code {child.returncode}")
    if early_exit:
        print(f"FAIL: tlwu --mic exited early with code {child.returncode}; the microphone check did not complete")
        return 1
    if seen:
        print("FAIL: non-loopback connections observed:\n  " + "\n  ".join(sorted(seen)))
        return 1
    print("PASS: no non-loopback connections observed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
