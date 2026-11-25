#!/usr/bin/env python3
"""README install GIF (#734 last part): run the real Linux installer (scripts/install.sh) from this checkout in an
xterm on an X display, record the terminal with ffmpeg x11grab, and speed the whole run up into a <= 15 s,
<= 5 MB GIF with scripts/readme_gif.py.

    python scripts/readme_gif_install.py --out-dir DIR [--max-wait 900]

Needs DISPLAY (Xvfb + a window manager), xterm, x11-utils (xwininfo), ffmpeg and the system packages install.sh
checks (git, curl, python3-venv, python3-tk, libsndfile1, libpulse0), so it asks no apt question. The `readme-gifs`
workflow sets that up (owner 2026-11-16: record on a GitHub-hosted runner, no owner recording). It is a real run:
install.sh clones this commit, creates the venv and pip-installs the app; only the model downloads are skipped
(TLWU_SKIP_MODELS=1) and it runs unattended (TLWU_YES=1), as the shown command says.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("readme_gif", ROOT / "scripts" / "readme_gif.py")
rg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rg)

GIF_NAME = "install_15s.gif"
TITLE = "tlwu-install"
BRANCH = "readme-gif-install"  # throwaway branch install.sh clones, like install-test.yml's ci-install-test
SHOWN = "TLWU_YES=1 TLWU_SKIP_MODELS=1 bash install.sh"  # the command line printed at the prompt = what really runs


def shell_line(repo: Path, ref: str, target: Path, hold: float = 3.0) -> str:
    """What the xterm runs: print the prompt, run install.sh unattended (TLWU_YES=1: it does not take the tty
    back, asks nothing and does not launch the GUI at the end; stdin closed), keep the last screen `hold` s, then
    exit with install.sh's code (written to target/../rc so the caller can read it)."""
    env = (f"TLWU_REPO={shlex.quote(str(repo))} TLWU_REF={shlex.quote(ref)} TLWU_DIR={shlex.quote(str(target))} "
           "TLWU_YES=1 TLWU_SKIP_MODELS=1 TLWU_NO_UPDATE=1 TLWU_UI_LANG=en")
    rc_file = shlex.quote(str(target.parent / "rc"))
    script = shlex.quote(str(repo / "scripts" / "install.sh"))
    return (f"printf '$ %s\\n' {shlex.quote(SHOWN)}; sleep 1; {env} bash {script} </dev/null; rc=$?; "
            f"echo $rc > {rc_file}; sleep {hold:g}; exit $rc")


def xterm_cmd(line: str, geometry: str = "100x30+0+0", font_size: int = 12) -> list[str]:
    return ["xterm", "-T", TITLE, "-geometry", geometry, "-fa", "Monospace", "-fs", str(font_size),
            "-bg", "white", "-fg", "black", "+sb", "-e", "bash", "-c", line]


def window_rect(xwininfo: str) -> tuple[int, int, int, int]:
    """(x, y, width, height) from `xwininfo -name …` output; ValueError when a field is missing."""
    vals = {}
    for key, pat in (("x", r"Absolute upper-left X:\s+(-?\d+)"), ("y", r"Absolute upper-left Y:\s+(-?\d+)"),
                     ("w", r"Width:\s+(\d+)"), ("h", r"Height:\s+(\d+)")):
        m = re.search(pat, xwininfo)
        if not m:
            raise ValueError(f"xwininfo output has no {key}")
        vals[key] = int(m.group(1))
    return vals["x"], vals["y"], vals["w"], vals["h"]


def even_rect(x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
    """Clip to the screen origin and make width / height even (libx264 yuv420p refuses odd sizes)."""
    x, y = max(0, x), max(0, y)
    return x, y, w - w % 2, h - h % 2


MIN_SIDE = 64  # px: xterm's window exists as 1x1 before the window manager maps and sizes it


def ready_rect(xwininfo: str) -> tuple[int, int, int, int] | None:
    """The even capture rect once the window is mapped and sized, else None. Readme-gifs run 45689028196 read the
    window before it was mapped: 1x1 became 0x0 and ffmpeg x11grab refused `-video_size 0x0`."""
    if not re.search(r"Map State:\s+IsViewable", xwininfo):
        return None
    try:
        x, y, w, h = even_rect(*window_rect(xwininfo))
    except ValueError:
        return None
    return (x, y, w, h) if min(w, h) >= MIN_SIDE else None


def wait_window(timeout: float = 20.0) -> tuple[int, int, int, int]:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        r = subprocess.run(["xwininfo", "-name", TITLE], capture_output=True, text=True)
        rect = ready_rect(r.stdout) if r.returncode == 0 else None
        if rect:
            return rect
        time.sleep(0.1)
    raise SystemExit(f"FAIL install: no mapped '{TITLE}' window of at least {MIN_SIDE} px after {timeout:g} s")


def drive(out_dir: Path, max_wait: float) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(ROOT), "branch", "-f", BRANCH, "HEAD"], check=True)
    work = Path(tempfile.mkdtemp(prefix="tlwu-install-gif-"))
    target = work / "tinglongwu"
    video = out_dir / "install.mp4"
    term = subprocess.Popen(xterm_cmd(shell_line(ROOT, BRANCH, target)))
    x, y, w, h = wait_window()
    cmd = rg.record_cmd(video, max_wait, os.environ.get("DISPLAY", ":99"), f"{w}x{h}", origin=(x, y))
    rec = subprocess.Popen(cmd)
    t0 = time.monotonic()
    try:
        term.wait(timeout=max_wait)
    except subprocess.TimeoutExpired:
        term.kill()
        print(f"FAIL install: install.sh still running after {max_wait:g} s", flush=True)
    rec.send_signal(signal.SIGINT)  # ffmpeg finishes the mp4 cleanly on SIGINT
    rec.wait(timeout=30)
    rc_file = work / "rc"
    rc = int(rc_file.read_text().strip()) if rc_file.exists() else -1
    print(f"install: install.sh exit {rc} after {time.monotonic() - t0:.0f} s; recorded {video.name}", flush=True)
    if rc != 0:
        print("FAIL install: install.sh did not succeed; no GIF made", flush=True)
        return 1
    secs, cap = rg.CAPS[GIF_NAME]
    try:
        return rg.main(["make", str(video), str(out_dir / GIF_NAME), "--max-seconds", f"{secs:g}",
                        "--max-mb", f"{cap / rg.MB:g}", "--whole"])
    except (subprocess.CalledProcessError, OSError, ValueError) as e:
        print(f"FAIL {GIF_NAME}: {e}", flush=True)
        return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--max-wait", type=float, default=900.0, help="s before the install counts as hung")
    a = ap.parse_args(argv)
    return drive(a.out_dir, a.max_wait)


if __name__ == "__main__":
    sys.exit(main())
