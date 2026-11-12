#!/usr/bin/env python3
"""Local pre-publish scan: look for denylisted words in tracked files and in the staged diff.

The word list lives OUTSIDE the repository (one word or phrase per line; blank lines and lines starting with
"#" are ignored; matching is case-insensitive). Default path: ~/.config/tlwu/denylist.txt, override with the
environment variable TLWU_DENYLIST. This script contains no keywords itself and never prints a matched word:
hits are reported as file:line with the match replaced by asterisks.

Usage:   python scripts/scan_denylist.py [--no-tracked] [--no-staged]
Exit:    0 clean, 1 hit(s) found, 2 denylist missing/empty/unreadable (or not a git repo).

Optional pre-commit hook (NOT installed by this script; do it yourself if you want it):
    printf '#!/bin/sh\\nexec python scripts/scan_denylist.py --no-tracked\\n' > .git/hooks/pre-commit
    chmod +x .git/hooks/pre-commit
(.git/hooks is local and is never pushed.) Run without arguments for a full scan before pushing.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def denylist_path() -> Path:
    env = os.environ.get("TLWU_DENYLIST")
    return Path(env).expanduser() if env else Path.home() / ".config" / "tlwu" / "denylist.txt"


def load_words(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig")
    return [w for w in (ln.strip() for ln in text.splitlines()) if w and not w.startswith("#")]


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, stdout=subprocess.PIPE).stdout


def mask_line(line: str, rx: re.Pattern) -> str:
    return rx.sub(lambda m: "*" * len(m.group(0)), line)


def scan_text(label: str, text: str, rx: re.Pattern, out: list[str]) -> None:
    for n, line in enumerate(text.splitlines(), 1):
        if rx.search(line):
            out.append(f"{label}:{n}: {mask_line(line.strip(), rx)[:160]}")


def scan_staged(rx: re.Pattern, out: list[str]) -> None:
    """Added lines of the staged diff, reported as file:line (line number in the new file)."""
    diff = git("diff", "--cached", "--no-color", "-U0", "--no-ext-diff").decode("utf-8", "replace")
    name, new_ln = "?", 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            name = line[4:].removeprefix("b/")
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            new_ln = int(m.group(1)) if m else 0
        elif line.startswith("+") and not line.startswith("+++"):
            if rx.search(line[1:]):
                out.append(f"{name}:{new_ln}: (staged) {mask_line(line[1:].strip(), rx)[:160]}")
            new_ln += 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # never crash on a legacy console codepage
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--no-tracked", action="store_true", help="skip the scan of tracked files")
    ap.add_argument("--no-staged", action="store_true", help="skip the scan of the staged diff")
    a = ap.parse_args()

    path = denylist_path()
    if not path.is_file():
        print(f"scan_denylist: denylist not found: {path}\n"
              f"  Create it (one word per line, outside the repo) or point TLWU_DENYLIST at it.", file=sys.stderr)
        return 2
    try:
        words = load_words(path)
    except (OSError, UnicodeDecodeError) as e:
        print(f"scan_denylist: cannot read {path}: {e}", file=sys.stderr)
        return 2
    if not words:
        print(f"scan_denylist: denylist is empty: {path}", file=sys.stderr)
        return 2
    rx = re.compile("|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)), re.IGNORECASE)

    hits: list[str] = []
    try:
        if not a.no_tracked:
            for rel in git("ls-files", "-z").decode("utf-8", "replace").split("\0"):
                if not rel:
                    continue
                f = ROOT / rel
                if rx.search(rel):  # the file NAME itself
                    hits.append(f"{rel}:0: (file name) {mask_line(rel, rx)}")
                try:
                    data = f.read_bytes()
                except OSError:
                    continue
                if b"\0" in data[:8192]:  # binary
                    continue
                scan_text(rel, data.decode("utf-8", "replace"), rx, hits)
        if not a.no_staged:
            scan_staged(rx, hits)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"scan_denylist: git failed ({e}); run inside the repository", file=sys.stderr)
        return 2

    for h in hits:
        print(h)
    if hits:
        print(f"scan_denylist: {len(hits)} hit(s) (matched words masked)", file=sys.stderr)
        return 1
    print(f"scan_denylist: clean ({len(words)} denylist entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
