"""Render the support tables and the contributors row from one source (docs/support.json + .all-contributorsrc).

The support tables sit between <!-- SUPPORT:START --> and <!-- SUPPORT:END --> in the release notes only; the READMEs
keep just the contributors row between <!-- CONTRIBUTORS:START --> and <!-- CONTRIBUTORS:END -->. The roadmap page
shows "what works today" from the same support.json.

  python scripts/render_support.py                 rewrite the contributors row in README.md / README.zh-TW.md / README.zh-CN.md,
                                                   and the "what works today" dot grid in docs/roadmap.html
  python scripts/render_support.py --check         exit 1 if a README row or the roadmap grid differs from the source (CI)
  python scripts/render_support.py --notes vX.Y.Z  print release notes: Download + block + What's new placeholder
  python scripts/render_support.py --notes-file F  rewrite the block inside an existing release-notes file
  python scripts/render_support.py --edit vX.Y.Z   rewrite the block inside that GitHub release's notes (needs gh)
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/sunososobro-hub/tinglongwu"
START, END = "<!-- SUPPORT:START -->", "<!-- SUPPORT:END -->"
READMES = {"en": "README.md", "zh-TW": "README.zh-TW.md", "zh-CN": "README.zh-CN.md"}
BLOCK = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
C_START, C_END = "<!-- CONTRIBUTORS:START -->", "<!-- CONTRIBUTORS:END -->"
C_BLOCK = re.compile(re.escape(C_START) + r".*?" + re.escape(C_END), re.S)
ROADMAP = "docs/roadmap.html"
G_START, G_END = "<!-- ROADMAP-GRID:START -->", "<!-- ROADMAP-GRID:END -->"
GRID = re.compile(re.escape(G_START) + r".*?" + re.escape(G_END), re.S)
DOT = {"🟢": "ok", "🟡": "ci", "⚪": "no-"}


def load(root: Path = ROOT) -> tuple[dict, list[dict]]:
    src = json.loads((root / "docs" / "support.json").read_text("utf-8"))
    people = json.loads((root / ".all-contributorsrc").read_text("utf-8"))["contributors"]
    return src, people


def _table(header: list[str], rows: list[tuple[str, list[str]]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|---|" + ":-:|" * (len(header) - 1)]
    for label, cells in rows:
        assert len(cells) == len(header) - 1, label
        out.append("| " + " | ".join([label, *cells]) + " |")
    return out


def contributors(src: dict, people: list[dict], loc: str) -> str:
    c = src["contributors"]
    cells = []
    for p in people:
        login = p["login"]
        emoji = " ".join(c["emoji"][t] for t in p["contributions"])  # KeyError = a type with no emoji in support.json
        cells.append(f'<td align="center"><a href="https://github.com/{login}"><img src="https://github.com/{login}.png?size=60" '
                     f'width="60" height="60" alt="{login}"><br><sub><b>{login}</b></sub></a><br>{emoji}</td>')
    return (f'<p><b>{c["heading"][loc].format(n=len(people))}</b></p><table><tr>{"".join(cells)}</tr></table>'
            f'<p><sub>{c["ai"][loc]}</sub><br><sub>{c["legend"][loc]}</sub></p>')


def block(src: dict, people: list[dict], loc: str) -> str:
    """The release-notes block (support tables + contributors), markers included."""
    dev, lang = src["device"], src["language"]
    lines = [START,
             *_table(dev["header"][loc], [(r["label"][loc], r["cells"]) for r in dev["rows"]]),
             "",
             *_table(lang["header"][loc], [(r["label"], r["cells"]) for r in lang["rows"]]),
             "",
             src["legend"][loc],
             "",
             contributors(src, people, loc) + END]
    return "\n".join(lines)


def readme_block(src: dict, people: list[dict], loc: str) -> str:
    """The README's contributors row, markers included (one line, so the README stays short)."""
    return C_START + contributors(src, people, loc) + C_END


def replace_block(text: str, new: str, name: str = "", pat: re.Pattern = BLOCK) -> str:
    if len(pat.findall(text)) != 1:
        raise SystemExit(f"{name}: expected exactly one {pat.pattern.split('.*?')[0]} ... block")
    return pat.sub(lambda m: new, text)


def _lab(tw: str, en: str, cn: str, cls: str) -> str:
    """A text cell; zh-TW is the visible text, the roadmap page script swaps in data-en / data-zh-cn."""
    a = lambda x: html.escape(x, quote=True)
    return f'<span class="{cls}" data-en="{a(en)}" data-zh-cn="{a(cn)}">{html.escape(tw)}</span>'


def _cell(v: str) -> str:
    """One status cell: a lit dot (optionally with a short note, e.g. 'Windows'), or a plain dash."""
    emoji, _, note = v.partition(" ")
    if emoji in DOT:
        return f'<span class="c"><i class="{DOT[emoji]}"></i>{html.escape(note)}</span>'
    return f'<span class="c same">{html.escape(v)}</span>'


def roadmap_grid(src: dict) -> str:
    """The "what works today" dot grid for docs/roadmap.html, markers included. No numbers are typed by hand."""
    dev, lang = src["device"], src["language"]
    out = [G_START, '<div class="today">']
    for cls, head, rows in (("g-os", dev["header"], [(r["label"], r["cells"]) for r in dev["rows"]]),
                            ("g-lang", lang["header"], [({"en": r["label"], "zh-TW": r["label"], "zh-CN": r["label"]}, r["cells"]) for r in lang["rows"]])):
        out += ['  <div class="panel">', f'    <div class="grid {cls}">', "      <span></span>" + "".join(
            _lab(t, e, c, "h") for e, t, c in zip(head["en"][1:], head["zh-TW"][1:], head["zh-CN"][1:]))]
        for label, cells in rows:
            out.append("      " + _lab(label["zh-TW"], label["en"], label["zh-CN"], "r") + "".join(_cell(v) for v in cells))
        out += ["    </div>", "  </div>"]
    out += ["</div>", G_END]
    return "\n".join(out)


def notes(tag: str, src: dict, people: list[dict]) -> str:
    base = f"{REPO_URL}/releases/download/{tag}/"
    download = "\n".join(src["download"]).format(ver=tag.lstrip("v"), base=base)
    return f"## Download\n{download}\n\n{block(src, people, 'en')}\n\n## What's new\n- TODO: at most 3 plain-language bullets, no PR numbers\n"


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true")
    g.add_argument("--notes", metavar="TAG")
    g.add_argument("--notes-file", metavar="PATH")
    g.add_argument("--edit", metavar="TAG", help="rewrite the block in that GitHub release's notes (uses gh)")
    a = ap.parse_args(argv)
    src, people = load(root)
    if a.notes:
        sys.stdout.buffer.write(notes(a.notes, src, people).encode("utf-8"))  # UTF-8 whatever the console code page
        return 0
    if a.notes_file:
        p = Path(a.notes_file)
        p.write_text(replace_block(p.read_text("utf-8-sig"), block(src, people, "en"), p.name), "utf-8", newline="\n")
        return 0
    if a.edit:  # fetch the release body, swap the block, upload it again; no file round trip through a shell
        import subprocess
        import tempfile
        body = subprocess.run(["gh", "release", "view", a.edit, "--json", "body", "-q", ".body"], check=True,
                              capture_output=True).stdout.decode("utf-8").lstrip("\ufeff")
        new = replace_block(body, block(src, people, "en"), a.edit)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "notes.md"
            f.write_text(new, "utf-8", newline="\n")
            subprocess.run(["gh", "release", "edit", a.edit, "--notes-file", str(f)], check=True,
                           stdout=subprocess.DEVNULL)
        return 0
    stale = []
    for loc, name in READMES.items():
        p = root / name
        old = p.read_text("utf-8")
        new = replace_block(old, readme_block(src, people, loc), name, C_BLOCK)
        if new != old:
            stale.append(name)
            if not a.check:
                p.write_text(new, "utf-8", newline="\n")
    rp = root / ROADMAP
    old = rp.read_text("utf-8")
    if len(GRID.findall(old)) != 1:
        raise SystemExit(f"{ROADMAP}: expected exactly one {G_START} ... {G_END} block")
    new = GRID.sub(lambda m: roadmap_grid(src), old)
    if new != old:
        stale.append(ROADMAP)
        if not a.check:
            rp.write_text(new, "utf-8", newline="\n")
    if a.check and stale:
        print("out of date (run python scripts/render_support.py):", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
