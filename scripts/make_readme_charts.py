"""Regenerate the README charts (docs/img/*.svg). Usage: python scripts/make_readme_charts.py

Data sources (no numbers are invented here):
  - verification matrix: the table under "Verification status" in docs/details.md
  - latency: the measurement table of the newest dated section in docs/latency.md
  - languages: the "Languages" table in docs/details.md (same data as docs/models.md)
SVGs have a transparent background and mid-tone colours so they read on GitHub light and dark themes.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "img"
TXT, MUTE = "#6e7781", "#7d8590"
OK, TODO, NA, BAR1, BAR2 = "#2da44e", "#bf8700", "#6e7781", "#388bfd", "#a371f7"
FONT = 'font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif"'


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;")


def svg(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
            f'aria-label="{esc(title)}" {FONT} font-size="14">\n<title>{esc(title)}</title>\n{body}</svg>\n')


def text(x, y, s, anchor="start", fill=TXT, size=14, weight="normal"):
    return (f'<text x="{x}" y="{y}" text-anchor="{anchor}" fill="{fill}" font-size="{size}" '
            f'font-weight="{weight}">{esc(s)}</text>\n')


def section(md, heading):
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", md, re.M | re.S)
    return m.group(1)


def table_rows(block):
    lines = [l.strip() for l in block.splitlines() if l.startswith("|")]
    rows = [[c.strip() for c in re.split(r"(?<!\\)\|", l.strip("|"))] for l in lines]
    return rows[2:]  # drop header + separator


def verification():
    rows = table_rows(section((ROOT / "docs/details.md").read_text("utf-8"), "Verification status"))
    cols = ["Windows", "macOS", "Linux"]
    x0, cw, rh, y0 = 190, 120, 40, 52
    w, h = x0 + cw * 3 + 10, y0 + rh * len(rows) + 40
    b = "".join(text(x0 + cw * i + cw / 2, 30, c, "middle", weight="bold") for i, c in enumerate(cols))
    for r, row in enumerate(rows):
        y = y0 + rh * r
        b += text(10, y + 25, row[0])
        for c in range(3):
            cell = row[c + 1]
            cx, cy = x0 + cw * c + cw / 2, y + rh / 2
            if cell.startswith("—"):
                fill, lab = NA, "n/a"
            elif cell.startswith("✅"):
                fill, lab = OK, "own side" if "own side" in cell else "verified"
            else:
                fill, lab = TODO, "not yet"
            b += (f'<rect x="{cx - 50}" y="{cy - 14}" width="100" height="28" rx="6" fill="{fill}" fill-opacity="0.85"/>'
                  + text(cx, cy + 5, lab, "middle", "#1f2328" if fill == TODO else "#ffffff", 13, "bold"))
    ly = y0 + rh * len(rows) + 24
    b += f'<rect x="10" y="{ly - 11}" width="12" height="12" rx="3" fill="{OK}"/>' + text(28, ly, "verified on a real device", size=12)
    b += f'<rect x="208" y="{ly - 11}" width="12" height="12" rx="3" fill="{TODO}"/>' + text(226, ly, "not yet verified", size=12)
    (OUT / "verification.svg").write_text(svg(w, h, b, "Verification matrix: feature by OS"), "utf-8")


def latency():
    md = (ROOT / "docs/latency.md").read_text("utf-8")
    first = re.search(r"^## (\d{4}-\d\d-\d\d)[^\n]*\n(.*?)(?=^## |\Z)", md, re.M | re.S)
    date = first.group(1)
    items = []
    for row in table_rows(first.group(2)):
        direction, lang, preset, lat = row[0], row[1], row[2], row[3]
        sec = float(re.match(r"([\d.]+) s", lat).group(1))
        label = "speak (first TTS audio)" if direction == "speak" else f"listen {lang} {preset}"
        items.append((label, sec, direction))
    x0, mx, bh, y0 = 190, 420, 28, 40
    scale = mx / 3.5
    w, h = x0 + mx + 80, y0 + (bh + 12) * len(items) + 40
    b = text(10, 22, f"End-to-end latency in seconds ({date}, RTX 3050 8 GB, smoke runs, indicative)", size=13, fill=MUTE)
    tx = x0 + 2.0 * scale
    yb = y0 + (bh + 12) * len(items) - 4
    b += f'<line x1="{tx}" y1="{y0 - 6}" x2="{tx}" y2="{yb}" stroke="{MUTE}" stroke-dasharray="4 4"/>'
    for i, (lab, sec, d) in enumerate(items):
        y = y0 + (bh + 12) * i
        b += text(10, y + 19, lab)
        b += (f'<rect x="{x0}" y="{y}" width="{sec * scale:.1f}" height="{bh}" rx="4" '
              f'fill="{BAR2 if d == "speak" else BAR1}" fill-opacity="0.85"/>')
        b += text(x0 + sec * scale - 8, y + 19, f"{sec:.2f} s", "end", "#ffffff", 14, "bold")
    b += text(tx, yb + 18, "2 s target", "middle", MUTE, 12)
    (OUT / "latency.svg").write_text(svg(w, h, b, "End-to-end latency per language and preset"), "utf-8")


def languages():
    rows = table_rows(section((ROOT / "docs/details.md").read_text("utf-8"), "Languages"))
    modes = ["Listen", "Speak", "Meeting"]
    x0, cw, rh, y0 = 250, 100, 38, 48
    w, h = x0 + cw * 3 + 10, y0 + rh * len(rows) + 36
    b = "".join(text(x0 + cw * i + cw / 2, 30, m, "middle", weight="bold") for i, m in enumerate(modes))
    for r, (name, code, status, mode) in enumerate(rows):
        y = y0 + rh * r
        codes = " ".join(re.findall(r"`(\w+)`", code))
        b += text(10, y + 24, f"{name} ({codes})" if "," not in name else f"experimental: {codes}")
        for c in range(3):
            on = "Listen only" in mode and c == 0 or mode.count(",") == 2
            fill = (OK if status == "focus" else TODO) if on else NA
            cx = x0 + cw * c + cw / 2
            b += (f'<rect x="{cx - 14}" y="{y + 9}" width="28" height="20" rx="5" fill="{fill}" '
                  f'fill-opacity="{0.85 if on else 0.25}"/>')
    ly = y0 + rh * len(rows) + 20
    b += f'<rect x="10" y="{ly - 11}" width="12" height="12" rx="3" fill="{OK}"/>' + text(28, ly, "focus", size=12)
    b += f'<rect x="100" y="{ly - 11}" width="12" height="12" rx="3" fill="{TODO}"/>' + text(118, ly, "experimental", size=12)
    (OUT / "languages.svg").write_text(svg(w, h, b, "Language support by mode"), "utf-8")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    verification()
    latency()
    languages()
    print("wrote", *sorted(p.name for p in OUT.glob("*.svg")))
