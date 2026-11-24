#!/usr/bin/env python3
"""README demo GIFs (#734): record an X display, turn the video into a size-capped GIF, check the caps.

The owner asked for the per-mode and install GIFs to be recorded in the cloud (Xvfb + ffmpeg x11grab on a
GitHub-hosted runner), not by hand. This is the tool half; the runner job drives the app and calls it.

    python scripts/readme_gif.py record OUT.mp4 --seconds 6 [--display :99] [--size 1280x720]
    python scripts/readme_gif.py make IN.mp4 OUT.gif --max-seconds 3 --max-mb 2 [--start 1.5]
    python scripts/readme_gif.py check [--dir assets/demo] [--present-only]

`check` reads the GIF frame delays with the stdlib only (no Pillow) and exits 1 when a GIF is missing or over its
cap (#734 Acceptance: mode GIFs <= 3 s and <= 2 MB, install GIF <= 15 s and <= 5 MB).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MB = 1024 * 1024
# name -> (max seconds, max bytes); #734 Acceptance
CAPS = {"mode_listen.gif": (3.0, 2 * MB), "mode_speak.gif": (3.0, 2 * MB), "mode_two_way.gif": (3.0, 2 * MB),
        "install_15s.gif": (15.0, 5 * MB)}
# tried in order until the GIF fits its byte cap: (frames per second, width in px)
LADDER = ((12, 720), (10, 640), (10, 560), (8, 560), (8, 480), (6, 480), (6, 400))


def gif_info(path: Path) -> tuple[int, float, int, int]:
    """(frames, duration in s, width, height) from the GIF block structure. A frame without a Graphic Control
    Extension counts 0 s (browsers show it ~0.1 s; the cap is about what we encode)."""
    b = Path(path).read_bytes()
    if b[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError(f"{path}: not a GIF")
    w, h, flags = int.from_bytes(b[6:8], "little"), int.from_bytes(b[8:10], "little"), b[10]
    i = 13 + (3 * 2 ** ((flags & 7) + 1) if flags & 0x80 else 0)
    frames, cs, delay = 0, 0, 0

    def skip_sub(j: int) -> int:
        while b[j]:
            j += b[j] + 1
        return j + 1

    while i < len(b):
        tag = b[i]
        if tag == 0x3B:  # trailer
            break
        if tag == 0x21:  # extension
            if b[i + 1] == 0xF9:
                delay = int.from_bytes(b[i + 4:i + 6], "little")
            i = skip_sub(i + 2)
        elif tag == 0x2C:  # image descriptor
            lf = b[i + 9]
            i += 10 + (3 * 2 ** ((lf & 7) + 1) if lf & 0x80 else 0)
            i = skip_sub(i + 1)  # LZW minimum code size, then the data sub-blocks
            frames, cs, delay = frames + 1, cs + delay, 0
        else:
            raise ValueError(f"{path}: bad block 0x{tag:02x} at {i}")
    return frames, cs / 100, w, h


def check(folder: Path, present_only: bool = False) -> list[tuple[str, bool, str]]:
    out = []
    for name, (secs, cap) in CAPS.items():
        p = folder / name
        if not p.exists():
            if not present_only:
                out.append((name, False, "missing"))
            continue
        frames, dur, w, h = gif_info(p)
        size = p.stat().st_size
        ok = frames > 0 and dur <= secs + 1e-6 and size <= cap
        out.append((name, ok, f"{frames} frames, {dur:.2f} s (cap {secs:g}), {size / MB:.2f} MB (cap {cap / MB:g}), "
                              f"{w}x{h}"))
    return out


def gif_cmd(src: Path, out: Path, start: float, seconds: float, fps: int, width: int) -> list[str]:
    """One ffmpeg call: trim, resample, scale, then palettegen + paletteuse (far smaller than the default palette)."""
    vf = (f"fps={fps},scale={width}:-2:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];"
          "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
    return ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:g}", "-t", f"{seconds:g}", "-i", str(src),
            "-filter_complex", vf, "-loop", "0", str(out)]


def fit(encode, max_bytes: int, ladder=LADDER):
    """Try each (fps, width) until encode(fps, width) returns a size <= max_bytes; returns that step or None."""
    for fps, width in ladder:
        if encode(fps, width) <= max_bytes:
            return fps, width
    return None


def make(src: Path, out: Path, max_seconds: float, max_bytes: int, start: float = 0.0):
    # 1/fps under the cap: ffmpeg rounds frame delays to centiseconds, never let the sum pass max_seconds
    def encode(fps: int, width: int) -> int:
        subprocess.run(gif_cmd(src, out, start, max_seconds - 1 / fps, fps, width), check=True)
        return out.stat().st_size

    step = fit(encode, max_bytes)
    if step is None:
        out.unlink(missing_ok=True)
    return step


def record_cmd(out: Path, seconds: float, display: str, size: str, fps: int = 15) -> list[str]:
    return ["ffmpeg", "-v", "error", "-y", "-f", "x11grab", "-video_size", size, "-framerate", str(fps),
            "-i", f"{display}+0,0", "-t", f"{seconds:g}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", str(out)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("record")
    p.add_argument("out", type=Path)
    p.add_argument("--seconds", type=float, required=True)
    p.add_argument("--display", default=":99")
    p.add_argument("--size", default="1280x720")
    p = sub.add_parser("make")
    p.add_argument("src", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--max-seconds", type=float, required=True)
    p.add_argument("--max-mb", type=float, required=True)
    p.add_argument("--start", type=float, default=0.0)
    p = sub.add_parser("check")
    p.add_argument("--dir", type=Path, default=ROOT / "assets" / "demo")
    p.add_argument("--present-only", action="store_true", help="skip GIFs that are not there yet")
    a = ap.parse_args(argv)
    if a.cmd in ("record", "make") and not shutil.which("ffmpeg"):
        print("ffmpeg not found (apt install ffmpeg)", file=sys.stderr)
        return 2
    if a.cmd == "record":
        return subprocess.run(record_cmd(a.out, a.seconds, a.display, a.size)).returncode
    if a.cmd == "make":
        step = make(a.src, a.out, a.max_seconds, int(a.max_mb * MB), a.start)
        if step is None:
            print(f"FAIL {a.out}: over {a.max_mb:g} MB even at {LADDER[-1][0]} fps / {LADDER[-1][1]} px")
            return 1
        frames, dur, w, h = gif_info(a.out)
        print(f"PASS {a.out}: {step[0]} fps, {w}x{h}, {frames} frames, {dur:.2f} s, "
              f"{a.out.stat().st_size / MB:.2f} MB")
        return 0
    rows = check(a.dir, a.present_only)
    for name, ok, detail in rows:
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
    return 0 if all(ok for _, ok, _ in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
