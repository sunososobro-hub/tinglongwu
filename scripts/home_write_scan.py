#!/usr/bin/env python3
"""Scan tracked files for code that changes a contributor's machine outside the repo (#1451, rules section 18).

Flags, in tracked files: `git config --global/--system`, `core.hooksPath` set to a value that is not repo-local
(repo-local = a relative path; absolute, `~`, `$VAR` and `..` paths are flagged), and writes to shell profiles / `.gitconfig`. Undoing what this repo wrote earlier (the #1309
cleanup) is allowlisted. Static text scan, no AI, no network.

Usage: python scripts/home_write_scan.py [--root DIR] [--json]   (prints "N findings"; exit 1 when N > 0)
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path

HOME = r"(~|\$HOME|\$\{HOME\}|%USERPROFILE%)/\.(bashrc|zshrc|profile|bash_profile|zprofile|gitconfig)\b"
# (rule name, regex applied per command segment). Git-level options (-C dir, -c k=v) may sit before `config`.
RULES = [
    ("git-config-global", re.compile(r"\bgit\b[^\n#]*\bconfig\b[^\n#]*--(global|system)\b")),
    ("shell-profile-write",
     re.compile(r"(>>?|\btee\b(\s+-a)?)\s*[\"']?" + HOME
                + r"|\bsed\s+-i\b[^\n]*[\"']?" + HOME
                + r"|\b(cp|mv|install|ln)\b[^\n]*\s[\"']?" + HOME + r"[\"']?\s*$")),
]
# core.hooksPath is judged by the assigned value (see hooks_path_value): repo-local = a relative path.
HOOKS_PATH = re.compile(r"\bcore\.hooksPath(?:=|\s+)(\S+)")
# A segment that only removes a setting is the allowlisted #1309 cleanup.
UNDO = re.compile(r"--unset\b|--remove-section\b|--unset-all\b|\brm\s+-rf?\b")
READ_ONLY = re.compile(r"--get(-all|-regexp)?\b|--list\b|\s-l\b")  # reading config changes nothing
ALLOW_FILES = {".claude/hooks/git_identity.sh"}  # the #1309 cleanup; only its undo / message lines are skipped
SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\|")
# Not scanned: the scanner, its tests, docs and rules that describe these patterns.
SKIP_PREFIXES = ("tests/", "docs/", ".agent/")
SKIP_FILES = {"scripts/home_write_scan.py"}
SKIP_SUFFIXES = (".md", ".png", ".jpg", ".ico", ".zip", ".whl", ".onnx", ".bin")


def hooks_path_value_is_local(value: str) -> bool:
    v = value.strip("\"'")
    if not v or v.startswith(("/", "~", "$", "%", "\\")) or re.match(r"[A-Za-z]:", v):
        return False
    n = posixpath.normpath(v)
    return not (n == ".." or n.startswith("../"))  # a relative path that climbs out of the repo is not local


def strip_comment(line: str) -> str:
    """Drop an unquoted shell comment (`#` at line start or after whitespace); `#` inside quotes stays."""
    quote = ""
    skip = False  # previous char was an unquoted/double-quoted backslash: this char is escaped
    for i, ch in enumerate(line):
        if skip:
            skip = False
            continue
        if ch == "\\" and quote != "'":
            skip = True
            continue
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i].rstrip()
    return line


def scan_text(path: str, text: str) -> list[dict]:
    out = []
    for no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            continue
        allowed_file = path in ALLOW_FILES
        if allowed_file and re.match(r"echo\b", stripped):
            bare = re.sub(r'"[^"]*"|\'[^\']*\'', "", stripped)  # drop quoted message text
            if not re.search(r"&&|\|\||;|\||>", bare):
                continue  # a lone printed message, not a write
        hit = set()
        for seg in SEGMENT_SPLIT.split(strip_comment(line)):
            if READ_ONLY.search(seg):
                continue
            if allowed_file and UNDO.search(seg):
                continue
            for name, rx in RULES:
                if rx.search(seg):
                    hit.add(name)
            if re.search(r"\bgit\b", seg):
                m = HOOKS_PATH.search(seg)
                if m and not hooks_path_value_is_local(m.group(1)):
                    hit.add("hooks-path")
        for name in sorted(hit):
            out.append({"file": path, "line": no, "rule": name, "text": stripped[:160]})
    return out


def scan_files(files: dict[str, str]) -> list[dict]:
    res = []
    for path in sorted(files):
        if path in SKIP_FILES or path.startswith(SKIP_PREFIXES) or path.endswith(SKIP_SUFFIXES):
            continue
        res += scan_text(path, files[path])
    return res


def scan_repo(root: Path) -> list[dict]:
    # drop repository-location overrides (GIT_INDEX_FILE, GIT_DIR, ...) so -C is honoured
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    names = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True, env=env).stdout
    files = {}
    for n in names.decode().split("\0"):
        p = root / n
        if not n or not p.is_file():
            continue
        try:
            files[n] = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
    return scan_files(files)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    found = scan_repo(Path(a.root))
    if a.json:
        print(json.dumps(found, indent=1))
    else:
        for f in found:
            print(f"{f['file']}:{f['line']} [{f['rule']}] {f['text']}")
    print(f"{len(found)} findings", file=sys.stderr if a.json else sys.stdout)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
