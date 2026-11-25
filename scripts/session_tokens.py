#!/usr/bin/env python3
"""#1916: token totals of the current Claude Code session, for the `tlwu-usage` trailer.

    python scripts/session_tokens.py [--minus "tokens_in=A tokens_out=B tokens_cache=C"] [transcript.jsonl]

Without a path it reads this session's transcript (`$CLAUDE_CODE_SESSION_ID.jsonl`), else the newest
`~/.claude/projects/*/*.jsonl`. A session can work up to 4 tickets: run it at claim time to note the baseline and
pass that output as `--minus` at PR open, so each PR reports only its own ticket. Prints
`tokens_in=<n> tokens_out=<n> tokens_cache=<n>` (in = fresh input + cache writes, cache = cache reads) or exits 1
with nothing on stdout when no usage can be read (then the trailer simply omits the fields).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def sum_usage(lines) -> dict[str, int] | None:
    """Sum `message.usage` over a transcript; a message id repeated across lines is counted once (last wins)."""
    by_id: dict[str, dict] = {}
    for n, line in enumerate(lines):
        try:
            msg = json.loads(line).get("message")
        except (ValueError, AttributeError):
            continue
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if isinstance(usage, dict):
            by_id[str(msg.get("id") or f"line{n}")] = usage
    if not by_id:
        return None
    tot = {"in": 0, "out": 0, "cache": 0}
    for u in by_id.values():
        tot["in"] += int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
        tot["out"] += int(u.get("output_tokens") or 0)
        tot["cache"] += int(u.get("cache_read_input_tokens") or 0)
    return tot


def trailer_fields(tot: dict[str, int]) -> str:
    return f"tokens_in={tot['in']} tokens_out={tot['out']} tokens_cache={tot['cache']}"


def parse_fields(text: str) -> dict[str, int]:
    """`tokens_in=A tokens_out=B tokens_cache=C` -> {"in": A, "out": B, "cache": C}; missing keys are 0."""
    return {k: int(m.group(1)) if (m := re.search(rf"\btokens_{k}=(\d+)", text)) else 0 for k in ("in", "out", "cache")}


def subtract(tot: dict[str, int], base: dict[str, int]) -> dict[str, int]:
    return {k: max(0, tot[k] - base.get(k, 0)) for k in tot}


def find_transcript(session_id: str | None, home: Path | None = None) -> Path | None:
    """This session's transcript when its id is known and the file exists, else the newest one."""
    root = (home or Path.home()) / ".claude" / "projects"
    if session_id:
        hit = sorted(root.glob(f"*/{session_id}.jsonl"))
        if hit:
            return hit[0]
    files = sorted(root.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def main(argv: list[str]) -> int:
    args = argv[1:]
    base = None
    if "--minus" in args:
        i = args.index("--minus")
        base = parse_fields(args[i + 1] if i + 1 < len(args) else "")
        del args[i:i + 2]
    path = Path(args[0]) if args else find_transcript(os.environ.get("CLAUDE_CODE_SESSION_ID"))
    if path is None:
        return 1
    try:
        with open(path, encoding="utf-8") as f:
            tot = sum_usage(f)
    except OSError:
        return 1
    if tot is None:
        return 1
    print(trailer_fields(subtract(tot, base) if base else tot))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
