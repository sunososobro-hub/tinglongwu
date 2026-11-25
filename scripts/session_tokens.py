#!/usr/bin/env python3
"""#1916: token totals of the current Claude Code session, for the `tlwu-usage` trailer.

    python scripts/session_tokens.py [transcript.jsonl]

Without an argument it reads the newest `~/.claude/projects/*/*.jsonl`. Prints
`tokens_in=<n> tokens_out=<n> tokens_cache=<n>` (in = fresh input + cache writes, cache = cache reads) or exits 1
with nothing on stdout when no usage can be read (then the trailer simply omits the fields).
"""
from __future__ import annotations

import json
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


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        path = Path(argv[1])
    else:
        files = sorted(Path.home().glob(".claude/projects/*/*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not files:
            return 1
        path = files[-1]
    try:
        with open(path, encoding="utf-8") as f:
            tot = sum_usage(f)
    except OSError:
        return 1
    if tot is None:
        return 1
    print(trailer_fields(tot))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
