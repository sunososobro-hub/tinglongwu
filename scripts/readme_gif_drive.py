#!/usr/bin/env python3
"""README mode GIFs (#734 part 2): run the real GUI on an X display, start one mode, play a speech clip into
PulseAudio, record the window with ffmpeg x11grab and cut a size-capped GIF with scripts/readme_gif.py.

    python scripts/readme_gif_drive.py listen|speak|two-way --out-dir DIR [--seconds 12] [--zh-wav ZH.wav]

Needs DISPLAY (Xvfb + a window manager), a PulseAudio null sink as the default sink whose monitor is also the
default source (listen hears the sink, speak's microphone is its monitor), the models in TLWU_MODELS and ffmpeg.
The `readme-gifs` workflow sets all of that up (owner 2026-11-16: record on a GitHub-hosted runner, no owner
recording). The demo is a real run: real models, real audio, real subtitles in the window.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIP_EN = ROOT / "src" / "core" / "tinglongwu" / "data" / "selftest_en.wav"
_spec = importlib.util.spec_from_file_location("readme_gif", ROOT / "scripts" / "readme_gif.py")
rg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rg)

# mode -> (App.toggle() argument, GIF name); "both" is gui.core.BOTH_MODE (#1001: listen + speak together)
MODES = {"listen": ("listen", "mode_listen.gif"), "speak": ("speak", "mode_speak.gif"),
         "two-way": ("both", "mode_two_way.gif")}
LOAD_TIMEOUT = 300.0  # s for the models to load on a CPU runner (e2e sees ~30-90 s)


def gui_settings(mode: str) -> dict:
    """gui.json for a clean demo: the other side speaks English, my language is Traditional Chinese; no first-run
    tour or usage question on top of the window; the 情境 row matches the mode (video = listen only)."""
    return {"lang": "en", "target": "zh-tw", "tour_seen": True, "usage_asked": True, "floating_bar": False,
            "scene": "video" if mode == "listen" else "meeting", "advanced_open": False, "theme": "light",
            "quality": "auto", "live_draft": True, "send_mode": "auto", "gpu_pack_never_ask": True,
            "vulkan_offer_never_ask": True}


def clips(mode: str, zh_wav: Path | None) -> list[Path]:
    """What is played into the sink: listen hears English, speak hears my Chinese, two-way hears both in turn."""
    if mode == "listen":
        return [CLIP_EN]
    if zh_wav is None:
        raise SystemExit(f"{mode}: --zh-wav is required (scripts/ci_e2e.py zh-wav makes one)")
    return [Path(zh_wav)] if mode == "speak" else [CLIP_EN, Path(zh_wav)]


def region(x: int, y: int, w: int, h: int, screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
    """Window rectangle clipped to the screen, width and height even (libx264 yuv420p refuses odd sizes)."""
    x, y = max(0, x), max(0, y)
    w, h = min(w, screen_w - x), min(h, screen_h - y)
    w, h = w - w % 2, h - h % 2
    if w < 2 or h < 2:
        raise ValueError(f"window {w}x{h} at {x},{y} is off the {screen_w}x{screen_h} screen")
    return x, y, w, h


def gif_start(first_line_s: float | None, seconds: float, gif_s: float = 3.0, lead: float = 2.0) -> float | None:
    """Where the 3 s GIF starts in the raw recording: `lead` s before the first translated line shows up, so the
    GIF has the line appearing in it. None = no translated line during the recording (the demo would be empty)."""
    if first_line_s is None or first_line_s > seconds:
        return None
    return round(min(max(0.0, first_line_s - lead), max(0.0, seconds - gif_s)), 2)


def translated(state) -> int:
    """Rows in the window that already have their translation (a pending row only shows the dim source)."""
    return sum(1 for _src, tgt in getattr(state, "finals", []) if tgt)


def play_script(played: list[Path]) -> list[str]:
    """One shell line: each clip into the default sink (the null sink both legs listen to), 0.5 s apart."""
    return ["sh", "-c", " ; ".join(f"paplay '{c}' ; sleep 0.5" for c in played)]


def drive(mode: str, out_dir: Path, seconds: float, zh_wav: Path | None) -> int:
    import tkinter  # noqa: F401 - fail early with a clear error when the runner's Python has no Tk

    from tinglongwu import offline
    from tinglongwu.gui.app import POLL_MS, App

    offline.set_mode("gui")  # same as tlwu-gui: the netguard is on for the demo run too

    toggle_arg, gif_name = MODES[mode]
    out_dir.mkdir(parents=True, exist_ok=True)
    played = clips(mode, zh_wav)
    appdata = Path(tempfile.mkdtemp(prefix="tlwu-gif-"))
    (appdata / "tinglongwu").mkdir()
    (appdata / "tinglongwu" / "gui.json").write_text(json.dumps(gui_settings(mode)), "utf-8")
    os.environ["APPDATA"] = str(appdata)
    app = App()
    # the runner has the models and the voice; an install offer would be a dialog in the demo, never a download
    for name in ("_offer_model", "_offer_pack", "_offer_voice", "_offer_cable"):
        setattr(app, name, lambda *_a, **_k: False)
    video = out_dir / f"{Path(gif_name).stem}.mp4"
    st: dict = {"rc": 1, "t0": time.monotonic()}

    def poll():
        if app.errored:  # the worker failed (missing model, no audio device): the log above says why
            print(f"FAIL {mode}: the session stopped with an error", flush=True)
            app.quit()
            return
        if app.load_t0 is not None or not app.listening:  # still loading (or not started yet)
            if time.monotonic() - st["t0"] > LOAD_TIMEOUT:
                print(f"FAIL {mode}: models not loaded after {LOAD_TIMEOUT:g} s", flush=True)
                app.quit()
                return
            app.root.after(250, poll)
            return
        r = app.root
        x, y, w, h = region(r.winfo_rootx(), r.winfo_rooty(), r.winfo_width(), r.winfo_height(),
                            r.winfo_screenwidth(), r.winfo_screenheight())
        cmd = rg.record_cmd(video, seconds, os.environ.get("DISPLAY", ":99"), f"{w}x{h}", origin=(x, y))
        st["rec"], st["rec_t0"] = subprocess.Popen(cmd), time.monotonic()
        st["play"] = subprocess.Popen(play_script(played))
        watch()

    def watch():  # first translated line: its time in the recording picks the GIF's start
        if "first" not in st and translated(app.state):
            st["first"] = time.monotonic() - st["rec_t0"]
        if st["rec"].poll() is None:
            app.root.after(100, watch)
        else:
            finish()

    def finish():
        st["play"].kill()
        start = gif_start(st.get("first"), seconds)
        print(f"{mode}: recorded {video.name}, {translated(app.state)} translated rows, first at "
              f"{st.get('first', -1):.1f} s", flush=True)
        if start is None:
            print(f"FAIL {mode}: no translated line in {seconds:g} s; no GIF made", flush=True)
        else:
            st["rc"] = rg.main(["make", str(video), str(out_dir / gif_name), "--max-seconds", "3", "--max-mb", "2",
                                "--start", f"{start:g}"])
        app.quit()

    app.root.after(POLL_MS, app.poll)  # App.run() without the update check and background model fetch
    app.root.after(1500, lambda: (app.toggle(toggle_arg), poll()))
    app.root.mainloop()
    if app.worker is not None:
        app.worker.join(timeout=2.0)
    return st["rc"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=sorted(MODES))
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--seconds", type=float, default=12.0, help="length of the raw recording")
    ap.add_argument("--zh-wav", type=Path, help="Chinese speech clip for speak / two-way")
    a = ap.parse_args(argv)
    rc = drive(a.mode, a.out_dir, a.seconds, a.zh_wav)
    sys.stdout.flush()
    os._exit(rc)  # like tlwu-gui: native threads (CTranslate2 / llama.cpp) can hang interpreter teardown


if __name__ == "__main__":
    sys.exit(main())
