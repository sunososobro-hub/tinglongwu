"""Render docs/details.md to a static page for GitHub Pages (docs.html). Usage: python scripts/build_docs_page.py OUT_DIR

Needs the `markdown` package (the stats-dashboard workflow installs it). Relative links are rewritten to the repo on GitHub.
"""
import re
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
BLOB = "https://github.com/sunososobro-hub/tinglongwu/blob/dev/"


def main(out: Path) -> None:
    src = ROOT / "docs" / "details.md"
    body = markdown.markdown(src.read_text("utf-8"), extensions=["tables"])

    def fix(m):
        u = m.group(1)
        if u.startswith(("http://", "https://", "mailto:", "#")):
            return m.group(0)
        path = (src.parent / u.partition("#")[0]).resolve().relative_to(ROOT).as_posix()
        return f'href="{BLOB}{path}"'

    body = body.replace("\|", "|")  # python-markdown keeps the table-cell escape inside code
    body = re.sub(r'href="([^"]+)"', fix, body)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>tinglongWu docs</title>
<style>:root{{color-scheme:light dark}}body{{max-width:860px;margin:2rem auto;padding:0 1rem;font:16px/1.55 system-ui,sans-serif}}
table{{border-collapse:collapse;display:block;overflow-x:auto}}td,th{{border:1px solid #8884;padding:.3rem .6rem;text-align:left}}code{{background:#8882;padding:0 .25em;border-radius:3px}}</style>
</head><body>
{body}
<p><a href="index.html">Stats dashboard</a> · <a href="roadmap.html">Roadmap</a></p>
</body></html>
"""
    out.mkdir(parents=True, exist_ok=True)
    (out / "docs.html").write_text(page, "utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
