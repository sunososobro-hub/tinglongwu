#!/usr/bin/env python3
"""Regenerate the iOS app icons from assets/icon/tinglong-dragon-head.svg.

iOS masks the icon itself, so the icon is a full-bleed navy square (no alpha, no rounded
corners) with the dragon head centred. Needs Pillow and `rsvg-convert` (librsvg) on PATH.
Usage: python3 mobile/tool/gen_ios_icons.py
"""
import json, subprocess, tempfile
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "assets/icon/tinglong-dragon-head.svg"
SET = ROOT / "ios/Runner/Assets.xcassets/AppIcon.appiconset"
NAVY = (0x2A, 0x31, 0x57)
BIG = 1024
FRAC = 0.66  # head's larger side as a fraction of the icon


def render_head() -> Image.Image:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "head.png"
        subprocess.run(["rsvg-convert", "-w", str(BIG), "-h", str(BIG), "-o", str(out), str(SVG)], check=True)
        img = Image.open(out).convert("RGBA")
        img.load()
    return img.crop(img.getbbox())


def master(head: Image.Image) -> Image.Image:
    s = BIG * FRAC / max(head.size)
    h = head.resize((round(head.width * s), round(head.height * s)), Image.LANCZOS)
    bg = Image.new("RGBA", (BIG, BIG), NAVY + (255,))
    bg.alpha_composite(h, ((BIG - h.width) // 2, (BIG - h.height) // 2))
    return bg.convert("RGB")  # App Store rejects icons with an alpha channel


def main():
    m = master(render_head())
    contents = json.loads((SET / "Contents.json").read_text())
    for e in contents["images"]:
        px = round(float(e["size"].split("x")[0]) * int(e["scale"].rstrip("x")))
        m.resize((px, px), Image.LANCZOS).save(SET / e["filename"])
    print("wrote", len(contents["images"]), "icons")


if __name__ == "__main__":
    main()
