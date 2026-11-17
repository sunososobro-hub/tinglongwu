#!/usr/bin/env python3
"""Local pre-publish scan: look for denylisted words in tracked files and in the staged diff.

The word list lives OUTSIDE the repository (one word or phrase per line; blank lines and lines starting with
"#" are ignored; matching is case-insensitive). Default path: ~/.config/tlwu/denylist.txt, override with the
environment variable TLWU_DENYLIST. CI passes the list TEXT instead (the repo secret) in TLWU_DENYLIST_TEXT, which
wins over the file when set (#1405). This script contains no keywords itself and never prints a matched word:
hits are reported as file:line with the match replaced by asterisks.

Matching (#1405): case-insensitive, full-width / half-width forms are equal (NFKC), and spaces are ignored on both
sides, so "a b" in the list also catches "ab" and "Ａ Ｂ". scripts/privacy_scan.py reuses this matcher.

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
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def denylist_path() -> Path:
    env = os.environ.get("TLWU_DENYLIST")
    return Path(env).expanduser() if env else Path.home() / ".config" / "tlwu" / "denylist.txt"


def parse_words(text: str) -> list[str]:
    text = text.removeprefix("\ufeff")
    return [w for w in (ln.strip() for ln in text.splitlines()) if w and not w.startswith("#")]


def load_words(path: Path) -> list[str]:
    return parse_words(path.read_text(encoding="utf-8-sig"))


def env_words() -> list[str] | None:
    """The list from TLWU_DENYLIST_TEXT (CI secret); None when that variable is unset or blank."""
    text = os.environ.get("TLWU_DENYLIST_TEXT", "")
    return parse_words(text) if text.strip() else None


def _fold(s: str) -> str:
    """Width-, case- and space-insensitive key of one string."""
    return "".join(c for c in unicodedata.normalize("NFKC", s).casefold() if not c.isspace())


_JOINERS = {"\uff9e", "\uff9f", "\u3099", "\u309a"}  # (half-width) voiced / semi-voiced marks


def _joins(c: str) -> bool:
    """True when c composes with the character before it (so NFKC must see both together)."""
    return unicodedata.combining(c) > 0 or c in _JOINERS or "\u1160" <= c <= "\u11ff"


def _groups(text: str):
    """(start, end) of each starter plus the combining / voiced marks / Hangul jamo that follow it."""
    i, n = 0, len(text)
    while i < n:
        j = i + 1
        while j < n and _joins(text[j]):
            j += 1
        yield i, j
        i = j


class Matcher:
    """Finds list words in text; the spans it returns are in the ORIGINAL text, so callers can mask them."""

    def __init__(self, words: list[str]):
        keys = sorted({k for k in (_fold(w) for w in words) if k}, key=len, reverse=True)
        self.size = len(keys)
        self._rx = re.compile("|".join(re.escape(k) for k in keys)) if keys else None

    def spans(self, text: str) -> list[tuple[int, int]]:
        if self._rx is None or not text:
            return []
        folded: list[str] = []
        start: list[int] = []  # folded char -> start of the original group it came from
        end: list[int] = []  # ... and the end of that group (combining marks stay inside a masked span)
        for a, b in _groups(text):
            for f in unicodedata.normalize("NFKC", text[a:b]).casefold():
                if not f.isspace():
                    folded.append(f)
                    start.append(a)
                    end.append(b)
        return [(start[m.start()], end[m.end() - 1]) for m in self._rx.finditer("".join(folded))]

    def search(self, text: str) -> bool:
        return bool(self.spans(text))

    def sub(self, text: str, repl=None) -> str:
        """Replace every match: by asterisks of the same length (default) or by the string `repl`."""
        out, last = [], 0
        for a, b in self.spans(text):
            out.append(text[last:a])
            out.append("*" * (b - a) if repl is None else repl)
            last = b
        out.append(text[last:])
        return "".join(out)


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, stdout=subprocess.PIPE).stdout


def mask_line(line: str, rx: Matcher) -> str:
    return rx.sub(line)


def added_lines(patch: str):
    """(new line number, text) of each added line of a unified diff hunk list (git diff -U0 or the API `patch`).

    Only lines after the first @@ count, so an added line that itself starts with "++" is still scanned."""
    new_ln, in_hunk = 0, False
    for line in patch.split("\n"):
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            new_ln, in_hunk = (int(m.group(1)) if m else 0), True
        elif not in_hunk:
            continue
        elif line.startswith("+"):
            yield new_ln, line[1:].removesuffix("\r")
            new_ln += 1
        elif line.startswith(" "):
            new_ln += 1


def scan_text(label: str, text: str, rx: Matcher, out: list[str]) -> None:
    for n, line in enumerate(text.splitlines(), 1):
        if rx.search(line):
            out.append(f"{mask_line(label, rx)}:{n}: {mask_line(line.strip(), rx)[:160]}")


def scan_staged(rx: Matcher, out: list[str]) -> None:
    """Added lines of the staged diff, reported as file:line (line number in the new file)."""
    diff = git("diff", "--cached", "--no-color", "-U0", "--no-ext-diff").decode("utf-8", "replace")
    for name, patch in _split_files(diff):
        for n, text in added_lines(patch):
            if rx.search(text):
                out.append(f"{mask_line(name, rx)}:{n}: (staged) {mask_line(text.strip(), rx)[:160]}")


def _split_files(diff: str):
    """(file name, its hunks) per file of a git diff; header lines are read only before the file's first @@."""
    name, buf, in_hunk = None, [], False
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            if name is not None:
                yield name, "\n".join(buf)
            name, buf, in_hunk = "?", [], False
        elif name is None:
            continue
        elif not in_hunk and line.startswith("+++ "):
            name = line[4:].removeprefix("b/")
        elif not in_hunk and not line.startswith("@@"):
            continue  # index / mode / --- lines
        else:
            in_hunk = True
            buf.append(line)
    if name is not None:
        yield name, "\n".join(buf)


def scan_tracked(rx: Matcher, out: list[str], root: Path = ROOT) -> None:
    for rel in subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True,
                              stdout=subprocess.PIPE).stdout.decode("utf-8", "replace").split("\0"):
        if not rel:
            continue
        if rx.search(rel):  # the file NAME itself
            out.append(f"{mask_line(rel, rx)}:0: (file name)")
        try:
            data = (root / rel).read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:  # binary
            continue
        scan_text(rel, data.decode("utf-8", "replace"), rx, out)


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

    words = env_words()
    if words is None:
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
    rx = Matcher(words)
    if not rx.size:
        print("scan_denylist: denylist is empty", file=sys.stderr)
        return 2

    hits: list[str] = []
    try:
        if not a.no_tracked:
            scan_tracked(rx, hits)
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
    print(f"scan_denylist: clean ({rx.size} denylist entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
