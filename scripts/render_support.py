"""Render the support tables and the contributors block from one source (docs/support.json + .all-contributorsrc).

The block sits between <!-- SUPPORT:START --> and <!-- SUPPORT:END --> in each README and in the release notes,
so the README top and the release page always show the same thing.

  python scripts/render_support.py                 rewrite the block in README.md / README.zh-TW.md / README.zh-CN.md
  python scripts/render_support.py --check         exit 1 if a README block differs from the source (CI)
  python scripts/render_support.py --notes vX.Y.Z  print release notes: Download + block + What's new placeholder
  python scripts/render_support.py --notes-file F  rewrite the block inside an existing release-notes file
  python scripts/render_support.py --edit vX.Y.Z   rewrite the block inside that GitHub release's notes (needs gh)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/sunososobro-hub/tinglongwu"
START, END = "<!-- SUPPORT:START -->", "<!-- SUPPORT:END -->"
READMES = {"en": "README.md", "zh-TW": "README.zh-TW.md", "zh-CN": "README.zh-CN.md"}
BLOCK = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)


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
    """The block, markers included. The END marker shares the last line (the README has a 60-line budget)."""
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


def replace_block(text: str, new: str, name: str = "") -> str:
    if len(BLOCK.findall(text)) != 1:
        raise SystemExit(f"{name}: expected exactly one {START} ... {END} block")
    return BLOCK.sub(lambda m: new, text)


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
                              capture_output=True).stdout.decode("utf-8").lstrip("﻿")
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
        new = replace_block(old, block(src, people, loc), name)
        if new != old:
            stale.append(name)
            if not a.check:
                p.write_text(new, "utf-8", newline="\n")
    if a.check and stale:
        print("out of date (run python scripts/render_support.py):", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
