#!/usr/bin/env python3
"""Print the whole tlwu rule set: `.agent/tlwu_rules.md` (the index) followed by every
`.agent/rules/*.md` section file it lists, in index order (#1917)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / ".agent" / "tlwu_rules.md"
_IMPORT = re.compile(r"^- @(\S+)", re.M)


def section_files(index: Path = INDEX) -> list[Path]:
    """The `@` imports of the index, resolved relative to the index (as Claude Code does)."""
    return [index.parent / m for m in _IMPORT.findall(index.read_text(encoding="utf-8"))]


def load(index: Path = INDEX) -> str:
    """Index text, then each listed section file; a missing file raises FileNotFoundError."""
    parts = [index.read_text(encoding="utf-8")]
    parts += [p.read_text(encoding="utf-8") for p in section_files(index)]
    return "\n".join(parts)


def headings(text: str) -> list[str]:
    """Numbered section headings (`## N. title`) in order."""
    return re.findall(r"(?m)^## (\d+\. .+)$", text)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    sys.stdout.write(load(Path(args[0]) if args else INDEX))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
