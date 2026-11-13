"""Render the TingLong mascot: SVG, PNG, .ico, favicon and the CLI splash frames. Needs Pillow (build time only).

Usage: python scripts/make_mascot.py

The drawing is a 1:1 port of dragon(g, time, mouthOpen, blink) and palette C from the owner-approved canvas draft
(200x120 logical canvas). It is recorded once as a list of primitives, then replayed by two back ends: SVG text and
Pillow (supersampled). Outputs:
  assets/mascot/tinglong.svg, tinglong.png, tinglong@2x.png       full dragon, mouth closed, eyes open, transparent
  assets/mascot/tinglong-head.svg, tinglong-head.png, ...@2x.png  square head-only crop for icons
  tinglongwu/gui/assets/tinglongwu.{ico,png,svg}                  app / window icon (head crop; .ico 16..256)
  docs/favicon.{ico,svg}                                          GitHub Pages favicon
  tinglongwu/data/splash_frames.json.gz                           half-block ANSI frames for the `tlwu` start splash
"""
from __future__ import annotations

import gzip
import io
import json
import math
import struct
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
W, H = 200, 120
C = dict(line="#4a5d9a", body="#8db8f0", hi="#e6f2ff", belly="#fff4e2", fin="#c9b8f5", ear="#f7bfd0",
         fuji="#6f8fd8", snow="#ffffff", eye="#ffffff", pupil="#2a3157", blush="#f6a5bd", tuft="#f3a6c8")
HEAD_BOX = (11, 0, 101, 90)  # square crop (x0, y0, x1, y1) around the head, whiskers included


def bez(p0, p1, p2, p3, t):
    u = 1 - t
    return (u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
            u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1])


class Rec:
    """Records primitives: ('ell', x, y, rx, ry, fill) / ('poly', pts, fill, stroke, lw) / ('line', pts, color, lw, curve)."""

    def __init__(self):
        self.ops = []

    def circle(self, x, y, r, fill):
        self.ops.append(("ell", x, y, r, r, fill))

    def ell(self, x, y, rx, ry, fill):
        self.ops.append(("ell", x, y, rx, ry, fill))

    def poly(self, pts, fill=None, stroke=None, lw=0):
        self.ops.append(("poly", pts, fill, stroke, lw))

    def line(self, pts, color, lw, curve=False):
        self.ops.append(("line", pts, color, lw, curve))


def dragon(g: Rec, time=0.0, mouth_open=False, blink=False, head_only=False):
    w = math.sin(time * 2.2) * 5
    P0, P1, P2, P3 = (64, 70), (92, 104 + w), (118, 42 - w), (148, 62)
    P4, P5, P6 = (178, 82 + w), (186, 100 + w), (196, 70 - w * .6)
    pts = [bez(P0, P1, P2, P3, i / 40) for i in range(41)] + [bez(P3, P4, P5, P6, i / 30) for i in range(1, 31)]
    n = len(pts)

    def rad(i):
        return 12.5 * (1 - 0.78 * i / (n - 1))

    if not head_only:
        for j, k in enumerate([round(n * .16), round(n * .3), round(n * .56), round(n * .68)]):
            p, r = pts[k], rad(k)
            step = math.sin(time * 4.4 + j * 1.6) * 2.5
            lx, ly = p[0] + (3 if j % 2 else -3) + step, p[1] + r + 7
            g.line([p, (lx, ly)], C["line"], 8.5)
            g.line([p, (lx, ly)], C["body"], 6)
            g.ell(lx + 1, ly + 1.5, 5, 3, C["line"])
            g.ell(lx + 1, ly + 1.5, 4, 2.2, C["body"])
            for d in (-2.6, 0, 2.6):
                g.circle(lx + 1 + d, ly + 3.2, 1.2, C["belly"])
        for i in range(4, n - 6, 5):
            p, r = pts[i], rad(i)
            g.poly([(p[0] - 3, p[1] - r + 1), (p[0], p[1] - r - 5), (p[0] + 3, p[1] - r + 1)], C["fin"], C["line"], .8)
        e = pts[n - 1]
        for dx, dy in ((0, -3), (3, 0), (0, 3)):
            g.circle(e[0] + dx + 2, e[1] + dy, 3, C["tuft"])
        for i in range(n - 1, -1, -1):
            g.circle(pts[i][0], pts[i][1], rad(i) + 1.4, C["line"])
        for i in range(n - 1, -1, -1):
            g.circle(pts[i][0], pts[i][1], rad(i), C["body"])
        for i in range(n - 1, -1, -1):
            g.circle(pts[i][0], pts[i][1] + rad(i) * .42, rad(i) * .42, C["belly"])
        for i in range(n - 1, -1, -2):
            g.circle(pts[i][0], pts[i][1] - rad(i) * .5, rad(i) * .18, C["hi"])
    for x, y in ((34, 32), (78, 32)):  # round ears
        g.circle(x, y, 15.5, C["line"])
        g.circle(x, y, 14, C["body"])
        g.circle(x, y, 8.5, C["ear"])
    # Fuji crest: flat top with a crater, snow cap
    g.poly([(41, 31), (50, 12.5), (53, 14), (56, 13.2), (59, 14), (62, 12.5), (71, 31)], C["fuji"], C["line"], 1.2)
    g.poly([(50, 12.5), (53, 14), (56, 13.2), (59, 14), (62, 12.5), (65, 19), (62.5, 17.6), (60.5, 20.2), (58, 18),
            (56, 20.6), (54, 18), (51.5, 20.2), (49.5, 17.6), (47, 19)], C["snow"])
    g.line([(50.6, 13.2), (53, 14.2), (56, 13.5), (59, 14.2), (61.4, 13.2)], C["line"], .9)
    # head
    g.circle(56, 50, 25.5, C["line"])
    g.circle(56, 50, 24, C["body"])
    g.ell(46, 37, 6, 3.5, C["hi"])
    g.ell(56, 64, 17.5, 11.5, C["line"])
    g.ell(56, 64, 16, 10, C["belly"])
    g.circle(51, 59, 1.4, C["line"])
    g.circle(61, 59, 1.4, C["line"])
    g.ell(38, 56, 4, 2.5, C["blush"])
    g.ell(74, 56, 4, 2.5, C["blush"])
    for x, y in ((46, 45), (66, 45)):
        if blink:
            g.line([(x - 5, y), (x + 5, y)], C["pupil"], 2)
        else:
            g.circle(x, y, 6.5, C["line"])
            g.circle(x, y, 5.5, C["eye"])
            g.circle(x + .5, y + .5, 3.4, C["pupil"])
            g.circle(x + 1.8, y - 1.4, 1.2, C["eye"])
    sway = math.sin(time * 3) * 3
    for s, dy in ((-1, 0), (1, 0), (-1, 5), (1, 5)):
        x0, y0 = 56 + s * 12, 61 + dy * .4
        cp = [(x0, y0), (x0 + s * 14, y0 - 4 + dy), (x0 + s * 20, y0 + 10 + dy + sway), (x0 + s * 30, y0 + 6 + dy - sway)]
        g.line(cp, C["line"], 1.6 if dy else 2.2, True)
        g.line(cp, C["fin"], .7 if dy else 1.1, True)
    if mouth_open:
        g.ell(56, 67.5, 5.5, 4, C["line"])
        g.ell(56, 67.5, 4.3, 2.9, C["ear"])
    else:  # arc(56, 64.5, r5, 0.25pi..0.75pi)
        arc = [(56 + 5 * math.cos(a), 64.5 + 5 * math.sin(a))
               for a in (0.25 * math.pi + i * 0.5 * math.pi / 12 for i in range(13))]
        g.line(arc, C["line"], 1.4)


# ---------------------------------------------------------------- back ends
def f(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def to_svg(rec: Rec, box=(0, 0, W, H), size=None) -> str:
    x0, y0, x1, y1 = box
    size = size or ((x1 - x0) * 2, (y1 - y0) * 2)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{f(x0)} {f(y0)} {f(x1 - x0)} {f(y1 - y0)}" '
           f'width="{f(size[0])}" height="{f(size[1])}" role="img" aria-label="TingLong, the listening dragon">']
    for op in rec.ops:
        if op[0] == "ell":
            _, x, y, rx, ry, fill = op
            out.append(f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(rx)}" fill="{fill}"/>' if rx == ry else
                       f'<ellipse cx="{f(x)}" cy="{f(y)}" rx="{f(rx)}" ry="{f(ry)}" fill="{fill}"/>')
        elif op[0] == "poly":
            _, pts, fill, stroke, lw = op
            d = " ".join(f"{f(a)},{f(b)}" for a, b in pts)
            st = f' stroke="{stroke}" stroke-width="{f(lw)}"' if stroke else ""
            out.append(f'<polygon points="{d}" fill="{fill}"{st}/>')
        else:
            _, pts, color, lw, curve = op
            if curve:
                d = "M{} {} C{}".format(f(pts[0][0]), f(pts[0][1]), " ".join(f"{f(a)} {f(b)}" for a, b in pts[1:]))
            else:
                d = "M" + " L".join(f"{f(a)} {f(b)}" for a, b in pts)
            out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{f(lw)}" stroke-linecap="round" '
                       f'stroke-linejoin="round"/>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) + (255,)


def to_image(rec: Rec, px_w: int, box=(0, 0, W, H), ss=4) -> Image.Image:
    """Rasterise with Pillow, `ss`x supersampled, to a px_w-wide RGBA image (aspect of `box`)."""
    x0, y0, x1, y1 = box
    px_h = round(px_w * (y1 - y0) / (x1 - x0))
    k = px_w * ss / (x1 - x0)
    im = Image.new("RGBA", (px_w * ss, px_h * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def T(p):
        return ((p[0] - x0) * k, (p[1] - y0) * k)

    def stroke(pts, color, lw):
        pp = [T(p) for p in pts]
        wpx = max(1, lw * k)
        d.line(pp, fill=rgb(color), width=round(wpx), joint="curve")
        for x, y in (pp[0], pp[-1]):  # round caps
            d.ellipse((x - wpx / 2, y - wpx / 2, x + wpx / 2, y + wpx / 2), fill=rgb(color))

    for op in rec.ops:
        if op[0] == "ell":
            _, x, y, rx, ry, fill = op
            cx, cy = T((x, y))
            d.ellipse((cx - rx * k, cy - ry * k, cx + rx * k, cy + ry * k), fill=rgb(fill))
        elif op[0] == "poly":
            _, pts, fill, st, lw = op
            d.polygon([T(p) for p in pts], fill=rgb(fill))
            if st:
                stroke(list(pts) + [pts[0], pts[1]], st, lw)
        else:
            _, pts, color, lw, curve = op
            if curve:
                pts = [bez(*pts, t / 24) for t in range(25)]
            stroke(pts, color, lw)
    return im.resize((px_w, px_h), Image.LANCZOS)


# ---------------------------------------------------------------- outputs
def write_ico(path: Path, img: Image.Image, sizes) -> None:
    pngs = []
    for s in sizes:
        b = io.BytesIO()
        img.resize((s, s), Image.LANCZOS).save(b, "PNG", optimize=True)
        pngs.append(b.getvalue())
    head = struct.pack("<HHH", 0, 1, len(sizes))
    off = 6 + 16 * len(sizes)
    dirs = b""
    for s, p in zip(sizes, pngs):
        dirs += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(p), off)
        off += len(p)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(head + dirs + b"".join(pngs))


def ansi_frame(im: Image.Image) -> str:
    """Half-block rows (upper/lower), 24-bit colour, escapes only where the colours change."""
    pw, ph = im.size
    px = im.load()
    rows = []
    for y in range(0, ph, 2):
        s, cur = [], None
        for x in range(pw):
            a, b = px[x, y], px[x, y + 1]
            ta, tb = a[3] > 110, b[3] > 110
            if not ta and not tb:
                sig, ch = "0", " "
            elif ta and tb:
                sig, ch = f"38;2;{a[0]};{a[1]};{a[2]};48;2;{b[0]};{b[1]};{b[2]}", "▀"
            elif ta:
                sig, ch = f"0;38;2;{a[0]};{a[1]};{a[2]}", "▀"
            else:
                sig, ch = f"0;38;2;{b[0]};{b[1]};{b[2]}", "▄"
            if sig != cur:
                s.append(f"\x1b[{sig}m")
                cur = sig
            s.append(ch)
        rows.append("".join(s) + "\x1b[0m")
    return "\n".join(rows)


def splash_frames(n=15, fps=10, cols=66, rows=40):
    """~1.5 s: mouth talking, one blink, body wave (time drives the wave, as in the draft)."""
    frames = []
    for i in range(n):
        r = Rec()
        dragon(r, time=i / fps, mouth_open=i % 4 in (1, 2), blink=i in (7, 8))
        im = to_image(r, cols * 4, ss=2).resize((cols, rows), Image.BOX)
        frames.append(ansi_frame(im))
    return {"w": cols, "h": rows // 2, "fps": fps, "frames": frames}


def main() -> None:
    full, head = Rec(), Rec()
    dragon(full)
    dragon(head, head_only=True)
    out = ROOT / "assets" / "mascot"
    out.mkdir(parents=True, exist_ok=True)
    (out / "tinglong.svg").write_text(to_svg(full), "utf-8")
    (out / "tinglong-head.svg").write_text(to_svg(head, HEAD_BOX, (180, 180)), "utf-8")
    for scale, suf in ((1, ""), (2, "@2x")):
        to_image(full, 400 * scale).save(out / f"tinglong{suf}.png", optimize=True)
        to_image(head, 128 * scale, HEAD_BOX).save(out / f"tinglong-head{suf}.png", optimize=True)
    icon_big = to_image(head, 256, HEAD_BOX, ss=4)
    ga = ROOT / "tinglongwu" / "gui" / "assets"
    write_ico(ga / "tinglongwu.ico", icon_big, (16, 24, 32, 48, 64, 128, 256))
    icon_big.save(ga / "tinglongwu.png", optimize=True)
    (ga / "tinglongwu.svg").write_text(to_svg(head, HEAD_BOX, (256, 256)), "utf-8")
    write_ico(ROOT / "docs" / "favicon.ico", icon_big, (16, 32, 48))
    (ROOT / "docs" / "favicon.svg").write_text(to_svg(head, HEAD_BOX, (64, 64)), "utf-8")
    data = ROOT / "tinglongwu" / "data" / "splash_frames.json.gz"
    raw = json.dumps(splash_frames(), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    with open(data, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0) as gz:
        gz.write(raw)
    print("wrote mascot assets; splash data", data.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
