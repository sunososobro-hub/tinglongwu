#!/usr/bin/env python3
"""Time install -> setup done -> first subtitle and write ttfs.json (stdlib only).

Usage:
  python scripts/ttfs.py --install "<cmd>" --setup "<cmd>" --out ttfs.json
                         [--tlwu "<cmd>"] [--clip PATH]

It measures; it does not gate: exit 0 even when over TTFS_BUDGET_S.
Exit 2 when no subtitle line appears within FIRST_SUBTITLE_TIMEOUT_S
(ttfs.json then has "ttfs_seconds": null).
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

TTFS_BUDGET_S = 180
TTFS_CLIP = "tinglongwu/data/selftest_en.wav"
BASELINE_MBPS = 50
FIRST_SUBTITLE_TIMEOUT_S = 600
TRANSLATION_PREFIX = "\u2192"  # tinglongwu/fmt.py: translation lines are '→ [TAG] text'

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UNITS = {"b": 1, "bytes": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9,
          "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3}
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(bytes|gib|mib|kib|gb|mb|kb|b)\b(?!\s*(?:/|per\s))", re.I)


_UNIT_NAMES = r"bytes|gib|mib|kib|gb|mb|kb|b"
# tinglongwu_setup.downloader.copy_with_progress: `download <key>: 35% (87/250 MB)` every 5 %, or `download <key>: 150 MB`
# every 50 MB when the size is unknown; the Kokoro / speaker files print `downloading <name> (~80 MB)` once.
_NUM = r"\d+(?:\.\d+)?"
_PROGRESS_RE = re.compile(r"^\s*download\s+(?P<key>\S+?):\s*(?:\d+%\s*\(\s*(?P<done>" + _NUM + r")\s*/\s*" + _NUM
                          + r"|(?P<abs>" + _NUM + r"))\s*(?P<unit>" + _UNIT_NAMES + r")\b", re.I)
_ANNOUNCE_RE = re.compile(r"^\s*downloading\s+(?P<key>\S+)\s*\(~?\s*(?P<size>" + _NUM + r")\s*(?P<unit>" + _UNIT_NAMES
                          + r")\)", re.I)


def parse_download_bytes(text):
    """Bytes downloaded, from the setup output (#855: each file once, not once per progress line).

    - `download <key>: N% (x/T MB)` / `download <key>: x MB`: the largest x seen for that key (bytes actually copied);
    - `downloading <name> (~S MB)`: S, only for a name that printed no progress line (a repeat counts once);
    - any other line that mentions 'download' and a size: every size on it, as before."""
    progress, announced, other = {}, {}, 0.0
    for line in text.splitlines():
        m = _PROGRESS_RE.match(line)
        if m:
            n = float(m.group("done") or m.group("abs")) * _UNITS[m.group("unit").lower()]
            progress[m.group("key")] = max(progress.get(m.group("key"), 0.0), n)
            continue
        m = _ANNOUNCE_RE.match(line)
        if m:
            announced[m.group("key")] = float(m.group("size")) * _UNITS[m.group("unit").lower()]
            continue
        if "download" not in line.lower():
            continue
        for num, unit in _SIZE_RE.findall(line):
            other += float(num) * _UNITS[unit.lower()]
    total = sum(progress.values()) + sum(v for k, v in announced.items() if k not in progress) + other
    return int(total)


def download_seconds_at_baseline(nbytes, mbps=BASELINE_MBPS):
    return nbytes * 8 / (mbps * 1e6)


def _run(cmd):
    """Run a shell command; return (seconds, returncode, stdout)."""
    t0 = time.monotonic()
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=ROOT)
    return time.monotonic() - t0, p.returncode, (p.stdout or "") + (p.stderr or "")


def time_first_subtitle(cmd, timeout=FIRST_SUBTITLE_TIMEOUT_S):
    """Seconds until the first translation line (fmt.py prefix '→'), or None on timeout/no output.

    The source line is printed before MT finishes, so a plain non-empty line is too early."""
    t0 = time.monotonic()
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True, encoding="utf-8", errors="replace", cwd=ROOT)
    found = []

    def reader():
        for line in p.stdout:
            if line.lstrip().startswith(TRANSLATION_PREFIX):
                found.append(time.monotonic() - t0)
                return

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    th.join(timeout)
    try:
        p.kill()
    except OSError:
        pass
    p.wait()
    return found[0] if found else None


def _version():
    try:
        with open(os.path.join(ROOT, "tinglongwu", "__init__.py"), encoding="utf-8") as f:
            m = re.search(r'__version__\s*=\s*["\']([^"\']+)', f.read())
        return m.group(1) if m else "unknown"
    except OSError:
        return "unknown"


def _sha():
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def measure(install_cmd, setup_cmd, tlwu_cmd, timeout=FIRST_SUBTITLE_TIMEOUT_S):
    inst_s, _, _ = _run(install_cmd)
    setup_s, _, setup_out = _run(setup_cmd)
    dl_s = download_seconds_at_baseline(parse_download_bytes(setup_out))
    first = time_first_subtitle(tlwu_cmd, timeout)
    res = {
        "ttfs_seconds": None, "download_seconds": round(dl_s, 3),
        "excl_download_seconds": None, "setup_seconds": round(setup_s, 3),
        "first_subtitle_seconds": None if first is None else round(first, 3),
        "os": sys.platform, "version": _version(), "sha": _sha(),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    if first is not None:
        total = inst_s + setup_s + first
        res["ttfs_seconds"] = round(total, 3)
        res["excl_download_seconds"] = round(total - dl_s, 3)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--install", required=True)
    ap.add_argument("--setup", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--clip", default=TTFS_CLIP)
    ap.add_argument("--tlwu", default=None,
                    help="command printing subtitles; translation lines must start with the arrow prefix to be "
                         "measured (default: tlwu --file CLIP --device cpu)")
    ap.add_argument("--timeout", type=float, default=FIRST_SUBTITLE_TIMEOUT_S)
    a = ap.parse_args(argv)
    tlwu = a.tlwu or "tlwu --file %s --device cpu" % shlex.quote(a.clip)
    res = measure(a.install, a.setup, tlwu, a.timeout)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    if res["ttfs_seconds"] is None:
        print("ttfs_seconds=null excl_download_seconds=null")
        return 2
    print("ttfs_seconds=%.1f excl_download_seconds=%.1f" % (
        res["ttfs_seconds"], res["excl_download_seconds"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
