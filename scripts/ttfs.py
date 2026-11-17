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
from datetime import datetime, UTC

TTFS_BUDGET_S = 180
TTFS_CLIP = "tinglongwu/data/selftest_en.wav"
BASELINE_MBPS = 50
FIRST_SUBTITLE_TIMEOUT_S = 600
TAIL_LINES = 40  # #1060: lines of each step's output printed when no subtitle arrives
TRANSLATION_PREFIX = "\u2192"  # tinglongwu/fmt.py: translation lines are '→ [TAG] text'
SOURCE_LINE_RE = re.compile(r"^\s*\d\d:\d\d:\d\d\s+[\u25b6\u25c0]")  # fmt.py head: '12:03:10 \u25c0 Them text'
PHASE_PREFIX = "==> "  # install.sh / mac_install.sh `say`: one line per installer step
SLOW_LINES = 5  # #1341: the N output lines that took longest to be followed by the next one
SLOW_LINE_MIN_S = 1.0  # shorter waits are not reported
LINE_CHARS = 120  # output lines are cut to this length in the phase report

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


def whisper_download_bytes(text):
    """#1360: bytes of the Whisper files in the setup output (the default listen path defers Hy-MT2 to the app)."""
    return parse_download_bytes("\n".join(ln for ln in text.splitlines() if "whisper" in ln.lower()))


def measured_download_seconds(stamps):
    """#1360: wall seconds from the first to the last `download ...` / `downloading ...` output line of a stamped run
    (the real download duration, not the bytes-at-baseline estimate). 0.0 when there is none. Pure."""
    ts = [t for t, line in stamps if _PROGRESS_RE.match(line) or _ANNOUNCE_RE.match(line)]
    return max(ts) - min(ts) if ts else 0.0


def first_source_subtitle(install_s, setup_s, first_source_s, download_s, whisper_bytes, mbps=BASELINE_MBPS):
    """#1360: (seconds, 50 Mbps baseline seconds) from install start to the first source-language subtitle line.
    The baseline replaces the measured download time by Whisper's bytes at MBPS (Hy-MT2 is not waited for). Pure."""
    total = install_s + setup_s + first_source_s
    return total, total - download_s + download_seconds_at_baseline(whisper_bytes, mbps)


def _run(cmd, stamps=None):
    """Run a shell command; return (seconds, returncode, stdout+stderr).

    STAMPS (a list), when given, collects (seconds since start, line) per output line (#1341: where the time goes).
    PYTHONUNBUFFERED makes the Python children (pip, tlwu-setup) print each line when it happens, not at exit."""
    t0 = time.monotonic()
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", cwd=ROOT, env=dict(os.environ, PYTHONUNBUFFERED="1"))
    lines = []
    for line in p.stdout:
        lines.append(line)
        if stamps is not None:
            stamps.append((time.monotonic() - t0, line.rstrip("\n")))
    p.wait()
    return time.monotonic() - t0, p.returncode, "".join(lines)


def phase_seconds(stamps, total):
    """[(installer step, seconds)] from stamped output lines (#1341): a step starts at its `==> ` line and ends at the
    next one (the last at TOTAL); output before the first `==> ` line is the step "(start)". Pure."""
    out, name, t_start = [], "(start)", 0.0
    for t, line in stamps:
        if line.startswith(PHASE_PREFIX):
            out.append((name, round(t - t_start, 1)))
            name, t_start = line[len(PHASE_PREFIX):].strip()[:LINE_CHARS], t
    out.append((name, round(total - t_start, 1)))
    return [(n, s) for n, s in out if not (n == "(start)" and s < SLOW_LINE_MIN_S)]


def slow_lines(stamps, total, n=SLOW_LINES, min_s=SLOW_LINE_MIN_S):
    """[(seconds until the next line, line)]: the N non-empty output lines followed by the longest silence, longest
    first (#1341: e.g. `Building wheel for llama-cpp-python` followed by 200 s of nothing). Pure."""
    rows = []
    for i, (t, line) in enumerate(stamps):
        nxt = stamps[i + 1][0] if i + 1 < len(stamps) else total
        if line.strip() and nxt - t >= min_s:
            rows.append((round(nxt - t, 1), line.strip()[:LINE_CHARS]))
    return sorted(rows, key=lambda r: -r[0])[:n]


def phase_report(stamps, total):
    """JSON-ready {"phases": [{"name", "seconds"}], "slow_lines": [{"seconds", "line"}]} for one installer run."""
    return {"phases": [{"name": n, "seconds": s} for n, s in phase_seconds(stamps, total)],
            "slow_lines": [{"seconds": s, "line": ln} for s, ln in slow_lines(stamps, total)]}


def _tail(text, n=TAIL_LINES):
    return "\n".join((text or "").splitlines()[-n:])


def time_first_subtitle(cmd, timeout=FIRST_SUBTITLE_TIMEOUT_S, out_lines=None, source_found=None):
    """Seconds until the first translation line (fmt.py prefix '→'), or None on timeout/no output.

    The source line is printed before MT finishes, so a plain non-empty line is too early.
    SOURCE_FOUND (a list), when given, gets the seconds of the first source-language line (#1360).
    OUT_LINES (a list), when given, collects the command's stdout+stderr lines read before the subtitle (#1060)."""
    t0 = time.monotonic()
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", cwd=ROOT)
    found = []

    def reader():
        for line in p.stdout:
            if out_lines is not None:
                out_lines.append(line.rstrip("\n"))
            if source_found is not None and not source_found and SOURCE_LINE_RE.match(line):
                source_found.append(time.monotonic() - t0)
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


def measure(install_cmd, setup_cmd, tlwu_cmd, timeout=FIRST_SUBTITLE_TIMEOUT_S, diag=None):
    """DIAG (a dict), when given, gets each step's exit code and output tail (#1060), for the no-subtitle report."""
    inst_stamps, setup_stamps = [], []
    inst_s, inst_rc, inst_out = _run(install_cmd, inst_stamps)
    setup_s, setup_rc, setup_out = _run(setup_cmd, setup_stamps)
    dl_s = download_seconds_at_baseline(parse_download_bytes(setup_out))
    tlwu_lines = []
    source = []
    first = time_first_subtitle(tlwu_cmd, timeout, tlwu_lines, source)
    if diag is not None:
        diag.update({"install": (inst_rc, _tail(inst_out)), "setup": (setup_rc, _tail(setup_out)),
                     "tlwu": (None, _tail("\n".join(tlwu_lines))),
                     "stamps": {"install": inst_stamps, "setup": setup_stamps}})
    res = {
        "ttfs_seconds": None, "download_seconds": round(dl_s, 3),
        "excl_download_seconds": None, "setup_seconds": round(setup_s, 3),
        "install_seconds": round(inst_s, 3),
        "first_subtitle_seconds": None if first is None else round(first, 3),
        "first_source_subtitle_seconds": None, "first_source_subtitle_baseline_seconds": None,
        "os": sys.platform, "version": _version(), "sha": _sha(),
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        # #1341: where the install and setup seconds go (installer steps + the lines followed by the longest wait)
        "install_phases": phase_report(inst_stamps, inst_s), "setup_phases": phase_report(setup_stamps, setup_s),
    }
    if source:  # #1360: Hy-MT2 deferred: the first source line does not wait for the translation model
        src, base = first_source_subtitle(inst_s, setup_s, source[0], measured_download_seconds(setup_stamps),
                                       whisper_download_bytes(setup_out))
        res["first_source_subtitle_seconds"] = round(src, 3)
        res["first_source_subtitle_baseline_seconds"] = round(base, 3)
    if first is not None:
        total = inst_s + setup_s + first
        res["ttfs_seconds"] = round(total, 3)
        res["excl_download_seconds"] = round(total - dl_s, 3)
    return res


def no_subtitle_report(diag):
    """#1060: why no subtitle came: exit code + last TAIL_LINES lines of install, setup and the subtitle command."""
    parts = []
    for step in ("install", "setup", "tlwu"):
        rc, tail = diag.get(step, (None, ""))
        head = "--- %s" % step + ("" if rc is None else " (exit %s)" % rc)
        parts.append("%s, last %d lines:\n%s" % (head, TAIL_LINES, tail or "(no output)"))
    return "\n".join(parts)


def stamped_text(stamps_by_step):
    """#1341: the installer output with the second each line appeared at, one `--- step` block per run."""
    parts = []
    for step in ("install", "setup"):
        parts.append("--- %s" % step)
        parts += ["%7.1f  %s" % (t, line) for t, line in stamps_by_step.get(step, [])]
    return "\n".join(parts) + "\n"


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
    ap.add_argument("--log", default=None, help="#1341: write the install + setup output with per-line seconds here")
    a = ap.parse_args(argv)
    tlwu = a.tlwu or "tlwu --file %s --device cpu" % shlex.quote(a.clip)
    diag = {}
    res = measure(a.install, a.setup, tlwu, a.timeout, diag)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    if a.log:
        with open(a.log, "w", encoding="utf-8") as f:
            f.write(stamped_text(diag.get("stamps", {})))
    if res["ttfs_seconds"] is None:
        print("ttfs_seconds=null excl_download_seconds=null")
        print(no_subtitle_report(diag), file=sys.stderr)
        return 2
    print("ttfs_seconds=%.1f excl_download_seconds=%.1f" % (
        res["ttfs_seconds"], res["excl_download_seconds"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
