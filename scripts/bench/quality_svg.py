#!/usr/bin/env python3
"""#1520: one picture of translation quality (faces grid, vs Google, wait time) for docs/benchmarks.md.

  python scripts/bench/quality_svg.py MATRIX_JSON GOOGLE_REFS_JSON > docs/assets/quality.svg

MATRIX_JSON = {"summaries": [...], "run": ...} or a list (as matrix_lights.py); thresholds are imported from
matrix_lights.py. Dev tool; the product never imports scripts/. No network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matrix_lights import GREEN_MIN, MATRIX6, YELLOW_MIN, light  # noqa: E402

LABEL = {"eng_Latn": "🇬🇧 EN", "rus_Cyrl": "🇷🇺 RU", "zho_Hans": "🇨🇳 简", "zho_Hant": "🇹🇼 繁", "jpn_Jpan": "🇯🇵 JA",
         "kor_Hang": "🇰🇷 KO"}
FACE = {"green": ("😀", "rgb(214,240,214)"), "yellow": ("🙂", "rgb(252,240,200)"), "red": ("😕", "rgb(252,224,222)")}
FONT = "Helvetica, Arial, 'Segoe UI Emoji', 'Apple Color Emoji', 'Noto Color Emoji', sans-serif"
INK = "rgb(42,36,24)"
# Median s/sentence (CPU, 4 vCPU runner) of the Hy-MT2 rows in docs/benchmarks.md: 1.06-2.42. Used only when the
# matrix json carries no per-pair `median_s`.
DEFAULT_WAIT = (1.06, 2.42)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cells(data) -> tuple[dict, dict]:
    summ = data["summaries"] if isinstance(data, dict) else data
    return {s["pair"]: s for s in summ if s.get("COMET") is not None}, (data if isinstance(data, dict) else {})


def render(data, google_refs: dict) -> str:
    cells, meta = _cells(data)
    refs = google_refs.get("refs", google_refs)
    o: list[str] = []

    def t(x, y, s, sz=18, c=INK, a="middle", w="normal"):
        o.append(f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{sz}" fill="{c}" text-anchor="{a}" '
                 f'font-weight="{w}">{_esc(str(s))}</text>')

    o.append('<rect x="1" y="1" width="898" height="598" rx="20" fill="rgb(255,251,240)" '
             'stroke="rgb(230,214,178)" stroke-width="2"/>')
    t(450, 46, "How well does tinglongWu translate?", 26, w="bold")
    t(450, 74, "Each face = one direction (from row → to column), scored on 200 news sentences", 15, "rgb(110,100,80)")
    x0, y0, s = 150, 130, 62
    for j, b in enumerate(MATRIX6):
        t(x0 + j * s + s / 2, y0 - 14, LABEL[b], 16, w="bold")
    cnt = {"green": 0, "yellow": 0, "red": 0}
    row_sum: dict[str, list] = {}
    for i, a in enumerate(MATRIX6):
        t(x0 - 12, y0 + i * s + s / 2 + 6, LABEL[a], 16, a="end", w="bold")
        for j, b in enumerate(MATRIX6):
            x, y = x0 + j * s, y0 + i * s
            if a == b:
                o.append(f'<rect x="{x + 3}" y="{y + 3}" width="{s - 6}" height="{s - 6}" rx="12" '
                         'fill="rgb(240,236,226)"/>')
                continue
            c = cells.get(f"{a}->{b}")
            if c is None:
                continue
            k = light(c["COMET"])
            cnt[k] += 1
            row_sum.setdefault(a, []).append(float(c["COMET"]))
            face, fill = FACE[k]
            o.append(f'<rect x="{x + 3}" y="{y + 3}" width="{s - 6}" height="{s - 6}" rx="12" fill="{fill}"/>')
            t(x + s / 2, y + s / 2 + 10, face, 28)
    ly = y0 + 6 * s + 34
    t(x0, ly, f"😀 good  {cnt['green']}", 17, a="start")
    t(x0 + 130, ly, f"🙂 fine  {cnt['yellow']}", 17, a="start")
    t(x0 + 260, ly, f"😕 weak  {cnt['red']}", 17, a="start")
    if row_sum:
        worst = min(row_sum, key=lambda r: sum(row_sum[r]) / len(row_sum[r]))
        t(x0, ly + 28, f"Weakest: {LABEL[worst][3:]} as the source", 15, "rgb(190,60,60)", a="start")
    rx = 600

    def card(y, h, title, lines, c):
        o.append(f'<rect x="{rx}" y="{y}" width="270" height="{h}" rx="16" fill="white" stroke="{c}" '
                 'stroke-width="2"/>')
        t(rx + 16, y + 30, title, 18, c, a="start", w="bold")
        for k, line in enumerate(lines):
            t(rx + 16, y + 58 + k * 24, line, 15, a="start")

    glines = []
    for pair, name in (("rus_Cyrl->zho_Hant", "RU → 繁"), ("eng_Latn->zho_Hant", "EN → 繁"),
                       ("rus_Cyrl->eng_Latn", "RU → EN")):
        if pair in cells and pair in refs:
            glines.append(f"{name}: {round(100 * cells[pair]['COMET'] / refs[pair]['COMET'])} / 100")
    card(110, 140, "🆚 vs Google Translate", glines + ["(Google = 100; ours is offline)"], "rgb(40,110,180)")
    med = [float(c["median_s"]) for c in cells.values() if c.get("median_s") is not None]
    lo, hi = (min(med), max(med)) if med else DEFAULT_WAIT
    card(270, 116, "⏱️ Wait per sentence", [f"{lo:.1f}–{hi:.1f} s on a plain 4-core CPU",
                                           "GPU: speed not measured yet"], "rgb(170,110,20)")
    card(406, 116, "❓ Not measured yet", ["Real meetings (your recordings)", "Speed on your own PC"],
         "rgb(150,150,150)")
    t(450, 585, f"Numbers: COMET scores in the table below · run {meta.get('run', '?')}", 12, "rgb(150,140,120)")
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 600" width="900" height="600" role="img">'
            '<title>Translation quality at a glance</title>'
            f'<desc>Faces: good COMET >= {GREEN_MIN:.3f}, fine {YELLOW_MIN:.3f}-{GREEN_MIN - 0.001:.3f}, '
            'weak below; comparison with Google Translate; wait per sentence.</desc>'.replace(">=", "&gt;=")
            + "".join(o) + "</svg>")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    data = json.loads(Path(argv[0]).read_text("utf-8"))
    google = json.loads(Path(argv[1]).read_text("utf-8"))
    sys.stdout.write(render(data, google) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
