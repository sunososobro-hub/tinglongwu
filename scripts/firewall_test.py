#!/usr/bin/env python3
"""Firewall-blocked offline test (#7): cut the network for tlwu's Python, run listen / speak / meeting on audio
files, and check every mode still translates and nothing even tried to reach the network.

  python scripts/firewall_test.py --listen-wav talk.wav --speak-wav zh.wav
  python scripts/firewall_test.py --speak-wav zh.wav --tts-python <kokoro venv python> --tts-model <kokoro dir>

--listen-wav: speech in any language (listen mode, translated to --to, default zh).
--speak-wav:  Chinese speech (speak mode zh->en and meeting mode zh->follow); any sample rate, resampled to 16 kHz.
Block method:
  Windows  run from an ADMIN terminal: adds an outbound Windows Firewall block rule for every Python involved
           (venv launcher + base interpreter + --tts-python), removes it at the end, also on errors / Ctrl+C.
  Linux    every mode runs in an empty network namespace (unshare -n -r; no root needed).
  macOS    block it yourself first (README "Firewall block test"), then add --already-blocked.
Before the modes run, a plain Python probe must FAIL to reach 1.1.1.1:443 / 8.8.8.8:53 (proves the block is live).
A mode passes when it exits 0, produced non-empty translations, and wrote nothing to netguard.jsonl.
Exit 0 = all modes passed, 1 = a mode failed, 2 = setup problem (no admin, block not active, missing input).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

RULE = "tlwu-firewall-test"
PROBE = ("import socket,sys\n"
         "for h,p in (('1.1.1.1',443),('8.8.8.8',53)):\n"
         "    try:\n"
         "        socket.create_connection((h,p),timeout=3).close(); print('OPEN'); sys.exit(0)\n"
         "    except OSError:\n"
         "        pass\n"
         "print('BLOCKED'); sys.exit(3)\n")


class SetupError(Exception):
    pass


@dataclass
class Mode:
    name: str
    cmd: list[str]
    check: Callable[[], tuple[bool, str]]
    log_dir: Path = field(default=Path("."))


# ---------- blockers ----------
class NoBlock:
    """--already-blocked: the user set up the firewall themselves."""
    label = "already blocked by the user"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def wrap(self, cmd: list[str]) -> list[str]:
        return cmd


class NamespaceBlock(NoBlock):
    """Linux: empty network namespace per command (only loopback, brought up when `ip` exists)."""
    label = "Linux network namespace (unshare -n -r)"

    def wrap(self, cmd: list[str]) -> list[str]:
        return ["unshare", "-n", "-r", "sh", "-c", 'ip link set lo up 2>/dev/null; exec "$@"', "sh", *cmd]


class WindowsFirewallBlock(NoBlock):
    """Windows: outbound block rule for each program; the rule is always deleted on exit."""
    label = "Windows Firewall outbound block rule"

    def __init__(self, programs: list[str], run=subprocess.run):
        self.programs, self.run, self.added = programs, run, False

    def __enter__(self):
        self._delete()  # leftover from an interrupted earlier run
        self.added = True
        for p in self.programs:
            r = self.run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={RULE}", "dir=out",
                          "action=block", f"program={p}", "enable=yes"], capture_output=True, text=True)
            if r.returncode != 0:
                self._delete()
                raise SetupError(f"could not add the firewall rule for {p}: {(r.stdout or '') + (r.stderr or '')}")
        return self

    def __exit__(self, *exc):
        self._delete()
        return False

    def _delete(self):
        self.run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={RULE}"], capture_output=True,
                 text=True)


def python_programs(extra=()) -> list[str]:
    """Every interpreter that may run tlwu code: a Windows venv python.exe is a launcher that starts the base one."""
    seen, out = set(), []
    for p in (sys.executable, getattr(sys, "_base_executable", None), *extra):
        for q in ((p, os.path.realpath(p)) if p else ()):
            k = os.path.normcase(q)
            if k not in seen and os.path.exists(q):
                seen.add(k)
                out.append(q)
    return out


def is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def pick_blocker(a, platform=sys.platform, admin=is_admin, run=subprocess.run):
    if a.already_blocked:
        return NoBlock()
    if platform == "win32":
        if not admin():
            raise SetupError("run this from an administrator terminal (needed to add the firewall rule)")
        return WindowsFirewallBlock(python_programs([a.tts_python] if a.tts_python else []), run)
    if platform.startswith("linux"):
        if shutil.which("unshare") is None:
            raise SetupError("unshare not found (util-linux); block the network yourself and use --already-blocked")
        return NamespaceBlock()
    raise SetupError("no automatic block on this OS: block Python in the firewall (README), then --already-blocked")


# ---------- checks ----------
def check_listen(path: Path, to: str) -> tuple[bool, str]:
    if not path.exists():
        return False, "no output file (nothing translated)"
    recs = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    good = [r for r in recs if r.get("src") and (r.get("dst") or r.get("lang") == to)]
    if not good:
        return False, f"{len(recs)} lines, none with a translation"
    return True, f"{len(good)} lines, e.g. {good[0]['src'][:30]!r} -> {good[0].get('dst', '')[:30]!r}"


def check_reverse(path: Path, tgt: str, tts: bool) -> tuple[bool, str]:
    if not path.exists():
        return False, "no result file"
    items = json.loads(path.read_text(encoding="utf-8")).get("items", [])
    if not items:
        return False, "no items"
    bad = [it.get("file") for it in items if not it.get("zh") or not it.get(tgt)]
    if bad:
        return False, f"empty transcript/translation for {bad}"
    if tts and not any(it.get("tts") for it in items):
        return False, "translated but no speech synthesized"
    return True, f"{len(items)} file(s), e.g. {items[0]['zh'][:30]!r} -> {items[0][tgt][:30]!r}"


def guard_entries(log_dir: Path) -> list[dict]:
    f = log_dir / "netguard.jsonl"
    if not f.exists():
        return []
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]


# ---------- plan / run ----------
def to_16k_mono(src: Path, dst: Path) -> Path:
    import numpy as np
    import soundfile as sf

    x, sr = sf.read(str(src), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if sr != 16000:
        from math import gcd

        from scipy.signal import resample_poly

        g = gcd(sr, 16000)
        x = resample_poly(x, 16000 // g, sr // g).astype(np.float32)
    sf.write(str(dst), x, 16000)
    return dst


def plan(a, tmp: Path, py: str = sys.executable) -> list[Mode]:
    modes = []
    if a.listen_wav:
        out = tmp / "listen.jsonl"
        modes.append(Mode("listen", [py, "-m", "tinglongwu", "--file", str(a.listen_wav), "--to", a.to,
                                     "--device", a.compute, "--save", str(out)],
                          lambda out=out: check_listen(out, a.to)))
    if a.speak_wav:
        wav = to_16k_mono(Path(a.speak_wav), tmp / "speak16k.wav")
        tts = bool(a.tts_python and a.tts_model)
        voice = (["--tts-engine", "kokoro", "--tts-python", a.tts_python, "--tts-model", a.tts_model,
                  "--out", str(tmp / "tts")] if tts else ["--no-tts"])
        base = [py, "-m", "tinglongwu.reverse.proto", "--input", str(wav), "--mt", a.mt, "--compute", a.compute,
                "--no-play", "--no-vmic", "--no-confirm"]
        for name, to in (("speak", ["--to", "en"]), ("meeting", ["--to", "follow", "--follow-file",
                                                                 str(tmp / "follow.txt")])):
            res = tmp / f"{name}.json"
            modes.append(Mode(name, base + to + voice + ["--save-result", str(res)],
                              lambda res=res: check_reverse(res, "en", tts)))
    for m in modes:
        m.log_dir = tmp / f"log_{m.name}"
    return modes


def run_mode(m: Mode, blocker, timeout: float, run=subprocess.run) -> tuple[bool, str]:
    env = {**os.environ, "TLWU_LOG_DIR": str(m.log_dir), "PYTHONIOENCODING": "utf-8"}
    env.pop("TLWU_ALLOW_NET", None)
    try:
        r = run(blocker.wrap(m.cmd), capture_output=True, text=True, encoding="utf-8", errors="replace",
                env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout:.0f} s"
    if r.returncode != 0:
        tail = " | ".join(((r.stderr or "") + (r.stdout or "")).strip().splitlines()[-3:])
        return False, f"exit {r.returncode}: {tail}"
    hits = guard_entries(m.log_dir)
    if hits:
        return False, "network attempt blocked by the guard: " + ", ".join(
            f"{h.get('package')}->{h.get('host')}:{h.get('port')}" for h in hits)
    return m.check()


def probe(blocker, run=subprocess.run) -> str:
    r = run(blocker.wrap([sys.executable, "-I", "-c", PROBE]), capture_output=True, text=True, timeout=30)
    return (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else "BLOCKED"


def main(argv=None, run=subprocess.run, platform=sys.platform, admin=is_admin) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--listen-wav", type=Path)
    ap.add_argument("--speak-wav", type=Path, help="Chinese speech for speak + meeting mode")
    ap.add_argument("--to", default="zh", help="listen mode target language (default zh)")
    ap.add_argument("--mt", default="opus", choices=["auto", "qwen", "opus"], help="speak/meeting MT (default opus)")
    ap.add_argument("--compute", default="auto", choices=["auto", "cpu", "cuda"])
    ap.add_argument("--tts-python", default="", help="Kokoro venv python (adds speech synthesis to the test)")
    ap.add_argument("--tts-model", default="", help="Kokoro model dir")
    ap.add_argument("--timeout", type=float, default=900, help="seconds per mode (default 900)")
    ap.add_argument("--already-blocked", action="store_true", help="you blocked the network yourself")
    a = ap.parse_args(argv)
    try:
        if not (a.listen_wav or a.speak_wav):
            raise SetupError("give --listen-wav and/or --speak-wav")
        for p in (a.listen_wav, a.speak_wav):
            if p and not p.is_file():
                raise SetupError(f"not found: {p}")
        blocker = pick_blocker(a, platform, admin, run)
        online = probe(NoBlock(), run) == "OPEN"
        results = []
        with tempfile.TemporaryDirectory(prefix="tlwu-fw-") as td, blocker:
            if probe(blocker, run) == "OPEN":
                raise SetupError(f"block not active: Python still reaches the internet ({blocker.label})")
            print(f"network blocked: {blocker.label}" + ("" if online else
                  " (note: this machine was offline before the block too)"), flush=True)
            for m in plan(a, Path(td)):
                print(f"[{m.name}] running...", flush=True)
                ok, detail = run_mode(m, blocker, a.timeout, run)
                results.append((m.name, ok, detail))
                print(f"[{m.name}] {'PASS' if ok else 'FAIL'} {detail}", flush=True)
    except SetupError as e:
        print(f"SETUP: {e}", file=sys.stderr)
        return 2
    passed = all(ok for _, ok, _ in results)
    print(("PASS" if passed else "FAIL") + ": " + ", ".join(f"{n} {'ok' if ok else 'FAIL'}" for n, ok, _ in results))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
