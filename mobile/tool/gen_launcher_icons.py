#!/usr/bin/env python3
"""Regenerate the Android launcher icons from mobile/assets/icon/tinglong-dragon-head.svg.

Needs Pillow and Microsoft Edge (headless) to rasterise the SVG; no runtime deps.
Usage: python mobile/tool/gen_launcher_icons.py [--edge PATH]
"""
import argparse, os, subprocess, tempfile
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "assets/icon/tinglong-dragon-head.svg"
RES = ROOT / "android/app/src/main/res"
NAVY = (0x2A, 0x31, 0x57, 255)
DENSITIES = {"mdpi": 1.0, "hdpi": 1.5, "xhdpi": 2.0, "xxhdpi": 3.0, "xxxhdpi": 4.0}
BIG = 1024
EDGES = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
         r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]


def render_head(edge: str) -> Image.Image:
    with tempfile.TemporaryDirectory() as td:
        html = Path(td) / "p.html"
        out = Path(td) / "o.png"
        html.write_text(
            "<html><body style='margin:0;background:transparent'>"
            f"<img src='{SVG.as_uri()}' width='{BIG}' height='{BIG}' style='display:block'>"
            "</body></html>")
        subprocess.run([edge, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--default-background-color=00000000",
                        f"--window-size={BIG},{BIG}", f"--screenshot={out}",
                        f"--user-data-dir={td}/prof", html.as_uri()],
                       check=True, capture_output=True, timeout=120)
        img = Image.open(out).convert("RGBA")
        img.load()
    bbox = img.getbbox()
    return img.crop(bbox)


def fit(head: Image.Image, box: int, frac: float) -> Image.Image:
    """Scale head so its larger side is frac*box, centred on a transparent box x box canvas."""
    s = box * frac / max(head.size)
    h = head.resize((max(1, round(head.width * s)), max(1, round(head.height * s))), Image.LANCZOS)
    canvas = Image.new("RGBA", (box, box), (0, 0, 0, 0))
    canvas.alpha_composite(h, ((box - h.width) // 2, (box - h.height) // 2))
    return canvas


def legacy(head: Image.Image, px: int) -> Image.Image:
    ss = 4
    n = px * ss
    bg = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ImageDraw.Draw(bg).rounded_rectangle((0, 0, n - 1, n - 1), radius=round(n * 0.18), fill=NAVY)
    bg.alpha_composite(fit(head, n, 0.85))
    return bg.resize((px, px), Image.LANCZOS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edge")
    a = ap.parse_args()
    edge = a.edge or next((e for e in EDGES if os.path.exists(e)), None)
    if not edge:
        raise SystemExit("msedge.exe not found; pass --edge")
    head = render_head(edge)
    legacy_px = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    for d, scale in DENSITIES.items():
        folder = RES / f"mipmap-{d}"
        folder.mkdir(parents=True, exist_ok=True)
        legacy(head, legacy_px[d]).save(folder / "ic_launcher.png")
        # Adaptive foreground: 108dp canvas, head inside the 66dp safe zone.
        fit(head, round(108 * scale), 66 / 108).save(folder / "ic_launcher_foreground.png")
    any26 = RES / "mipmap-anydpi-v26"
    any26.mkdir(parents=True, exist_ok=True)
    (any26 / "ic_launcher.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@color/ic_launcher_background"/>\n'
        '    <foreground android:drawable="@mipmap/ic_launcher_foreground"/>\n'
        '</adaptive-icon>\n')
    (RES / "values/ic_launcher_background.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
        '    <color name="ic_launcher_background">#2a3157</color>\n</resources>\n')


if __name__ == "__main__":
    main()
