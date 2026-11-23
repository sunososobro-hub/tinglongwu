#!/usr/bin/env python3
"""#1689: check built-in term files (tinglongwu/data/terms/<domain>.tsv) before a PR. Format: docs/glossary-domains.md.

    python scripts/terms_check.py                      # every file in tinglongwu/data/terms/
    python scripts/terms_check.py FILE.tsv [FILE ...]  # these files (ids are also checked against the shipped ones)

Errors: line 1 is not `# scenario: <key>`; the file name is not a lower-case domain name; the header does not start
with `id` followed by the six base languages (en ru ja ko zh-TW zh-CN; more language columns may follow); a row whose
cell count differs from the header; an id that is not a lower-case ascii slug or is used twice (in any file); a row
with no term at all; a malformed cell (empty spelling between `|`, `*` other than at the start of a spelling, `=`,
a spelling that is not trimmed); a spelling repeated within one language column of a file (lower-case, ё = е).
Exit 1 on errors."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tinglongwu.glossary import TERM_LANGS, TERMS_DIR, read_terms, spellings  # noqa: E402

_DOMAIN = re.compile(r"^[a-z][a-z0-9_-]*$")
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_SCENARIO = re.compile(r"^#\s*scenario:\s*(\*|[a-z0-9_-]+)(\s*,\s*(\*|[a-z0-9_-]+))*\s*$")


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("ё", "е").split())


def check_text(text: str, name: str, ids: dict | None = None) -> list[str]:
    """Errors for one file (`name` = file name). `ids` (id -> file) carries ids across files."""
    ids = {} if ids is None else ids
    errs = []
    stem = Path(name).stem
    if not _DOMAIN.match(stem):
        errs.append(f"{name}: file name must be a lower-case domain name (a-z, 0-9, _ or -) + .tsv")
    first = text.lstrip("﻿").splitlines()[:1]
    if not first or not _SCENARIO.match(first[0].strip()):
        errs.append(f"{name}: line 1 must be '# scenario: <key>' (lower-case key, e.g. tech)")
    tb = read_terms(text, stem)
    want = ["id", *TERM_LANGS]
    if tb.header[:len(want)] != want or len(set(tb.header)) != len(tb.header) or "" in tb.header:
        errs.append(f"{name}: header must be TAB-separated {' '.join(want)} (extra language columns may follow), "
                    f"got {' '.join(tb.header) or 'nothing'}")
        return errs
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")][1:]
    seen: dict = {}
    for n, (raw, row) in enumerate(zip(lines, tb.rows), 1):
        rid = row.get("id", "")
        where = f"{name}: row {n} ({rid or '?'})"
        if len(raw.rstrip("\r\n").split("\t")) != len(tb.header):
            errs.append(f"{where}: needs {len(tb.header)} TAB-separated cells")
        if not _ID.match(rid) or not rid.isascii():
            errs.append(f"{where}: id must be a lower-case ascii slug (a-z, 0-9, '_', '.', '-')")
        elif rid in ids:
            errs.append(f"{where}: id already used in {ids[rid]}")
        else:
            ids[rid] = name
        if not any(spellings(row.get(c, "")) for c in tb.langs):
            errs.append(f"{where}: row has no term")
        for col in tb.langs:
            cell = row.get(col, "")
            if not cell:
                continue
            parts = cell.split("|")
            if any(not p.strip() for p in parts):
                errs.append(f"{where} [{col}]: empty spelling between '|'")
            if any("*" in p.strip().lstrip("*") or p.strip() == "*" for p in parts) or "=" in cell:
                errs.append(f"{where} [{col}]: '*' only at the start of a spelling; no '='")
            for sp in spellings(cell):
                key = _norm(sp)
                prev = seen.setdefault(col, {}).get(key)
                if prev:
                    errs.append(f"{where} [{col}]: '{sp}' repeats a spelling of {prev}")
                else:
                    seen[col][key] = rid
    return errs


def check_files(paths) -> list[str]:
    ids: dict = {}
    errs = []
    for p in paths:
        p = Path(p)
        errs += check_text(p.read_text(encoding="utf-8-sig"), p.name, ids)
    return errs


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    given = [Path(a) for a in args]
    shipped = sorted(TERMS_DIR.glob("*.tsv"))
    if not (given or shipped):
        print("terms_check: no .tsv files", file=sys.stderr)
        return 1
    mine = {p.resolve() for p in given}
    paths = [p for p in shipped if p.resolve() not in mine] + given  # given files: ids also checked vs shipped
    errs = check_files(paths)
    for e in errs:
        print(e)
    shown = given or shipped
    rows = sum(len(read_terms(p.read_text(encoding="utf-8-sig")).rows) for p in shown)
    print(f"terms_check: {len(shown)} file(s), {rows} rows, {len(errs)} error(s)", file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
