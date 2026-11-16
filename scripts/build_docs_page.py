"""Render docs/details.md to a static page for GitHub Pages (docs.html). Usage: python scripts/build_docs_page.py OUT_DIR

Needs the `markdown` package (the stats-dashboard workflow installs it). Relative links are rewritten to the repo on GitHub.
"""
import html
import json
import re
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
BLOB = "https://github.com/sunososobro-hub/tinglongwu/blob/dev/"


PIPELINE_SVG = """<svg class="flow" viewBox="0 0 760 120" role="img" aria-label="Sound in, speech recognition, translation, then subtitles or voice out, all on your device">
<rect x="2" y="2" width="756" height="116" rx="14" fill="none" stroke="currentColor" stroke-opacity=".35" stroke-dasharray="6 4"/>
<g font-family="system-ui,sans-serif" font-size="15" text-anchor="middle">
<rect x="16" y="34" width="130" height="52" rx="10" fill="#2b7de9"/><text x="81" y="64" fill="#fff">Sound in</text>
<rect x="196" y="34" width="130" height="52" rx="10" fill="#7a4fd8"/><text x="261" y="64" fill="#fff">ASR (hear)</text>
<rect x="376" y="34" width="130" height="52" rx="10" fill="#c2561a"/><text x="441" y="64" fill="#fff">MT (translate)</text>
<rect x="556" y="34" width="190" height="52" rx="10" fill="#1d8a4f"/><text x="651" y="58" fill="#fff">Subtitles</text><text x="651" y="77" fill="#fff">or voice out</text>
</g>
<g stroke="currentColor" stroke-width="2.5" fill="currentColor"><path d="M148 60h40"/><path d="M328 60h40"/><path d="M508 60h40"/>
<path d="M188 60l-8-5v10z"/><path d="M368 60l-8-5v10z"/><path d="M548 60l-8-5v10z"/></g>
<text x="380" y="108" font-family="system-ui,sans-serif" font-size="13" text-anchor="middle" fill="currentColor">All on your device. No cloud, no upload.</text>
</svg>"""

LIGHT = {"🟢": ("ok", "Works"), "🟡": ("ci", "CI / eval only"), "⚪": ("no", "Untested")}


def support_grid() -> str:
    """Language support grid (docs/support.json) as a colour-coded HTML table."""
    lang = json.loads((ROOT / "docs" / "support.json").read_text("utf-8"))["language"]
    head = "".join(f"<th>{html.escape(h)}</th>" for h in lang["header"]["en"])
    rows = []
    for r in lang["rows"]:
        cells = []
        for c in r["cells"]:
            icon = c.split(" ")[0]
            cls, title = LIGHT.get(icon, ("na", "Same language"))
            note = c[len(icon):].strip()
            cells.append(f'<td class="{cls}" title="{title}">{html.escape(icon + (" " + note if note else ""))}</td>')
        rows.append(f"<tr><th>{html.escape(r['label'])}</th>{''.join(cells)}</tr>")
    return (
        '<div class="grid"><table><thead><tr>' + head + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
        '<p class="legend">🟢 works on a real device · 🟡 CI / eval only · ⚪ untested · — same language</p>'
    )


def main(out: Path) -> None:
    src = ROOT / "docs" / "details.md"
    body = markdown.markdown(src.read_text("utf-8"), extensions=["tables"])

    def fix(m):
        u = m.group(1)
        if u.startswith(("http://", "https://", "mailto:", "#")):
            return m.group(0)
        path, _, frag = u.partition("#")
        path = (src.parent / path).resolve().relative_to(ROOT).as_posix()
        return f'href="{BLOB}{path}{"#" + frag if frag else ""}"'

    body = body.replace(r"\|", "|")  # python-markdown keeps the table-cell escape inside code
    body = re.sub(r'href="([^"]+)"', fix, body)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>tinglongWu docs</title>
<link rel="icon" href="favicon.svg" type="image/svg+xml"><link rel="icon" href="favicon.ico" sizes="any">
<style>:root{{color-scheme:light dark}}body{{max-width:860px;margin:2rem auto;padding:0 1rem;font:16px/1.55 system-ui,sans-serif}}
pre{{overflow-x:auto}}code{{overflow-wrap:anywhere}}.flow{{width:100%;height:auto;margin:1rem 0}}.grid{{overflow-x:auto}}.grid table{{width:100%}}td.ok{{background:#1d8a4f33}}td.ci{{background:#e0b40033}}td.no{{background:#8881}}.legend{{font-size:.85rem;opacity:.8}}
table{{border-collapse:collapse;display:block;overflow-x:auto}}td,th{{border:1px solid #8884;padding:.3rem .6rem;text-align:left}}code{{background:#8882;padding:0 .25em;border-radius:3px}}</style>
</head><body>
<h1>How tinglongWu works</h1>
{PIPELINE_SVG}
<h2>Language support</h2>
{support_grid()}
{body}
<p><a href="index.html">Stats dashboard</a> · <a href="roadmap.html">Roadmap</a></p>
</body></html>
"""
    out.mkdir(parents=True, exist_ok=True)
    (out / "docs.html").write_text(page, "utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
