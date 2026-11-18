#!/usr/bin/env python3
"""Scan tracked files for code that changes a contributor's machine outside the repo (#1451, rules section 18).

Flags, in tracked files: `git config --global/--system`, `core.hooksPath` set to something other than a
repo-local value, and writes to shell profiles / `.gitconfig`. Undoing what this repo wrote earlier (the #1309
cleanup) is allowlisted. Static text scan, no AI, no network.

Usage: python scripts/home_write_scan.py [--root DIR] [--json]   (prints "N findings"; exit 1 when N > 0)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# (rule name, regex applied per line)
RULES = [
    ("git-config-global", re.compile(r"git\s+config\b[^\n#]*--(global|system)\b")),
    ("hooks-path", re.compile(r"git\s+config\b[^\n#]*\bcore\.hooksPath\b|\bgit\s+-c\s+core\.hooksPath=")),
    ("shell-profile-write",
     re.compile(r"(>>?|\btee\b(\s+-a)?|\bsed\s+-i\b[^\n]*|\bcp\b[^\n]*|\bmv\b[^\n]*)\s*[\"']?(~|\$HOME|\$\{HOME\}|%USERPROFILE%)/\.(bashrc|zshrc|profile|bash_profile|zprofile|gitconfig)\b")),
]
# A line that only removes a setting is the allowlisted #1309 cleanup.
UNDO = re.compile(r"--unset\b|--remove-section\b|--unset-all\b|\brm\s+-rf?\b|^\s*echo\b")
READ_ONLY = re.compile(r"--get(-all|-regexp)?\b|--list\b|\s-l\b")  # reading config changes nothing
ALLOW_FILES = {".claude/hooks/git_identity.sh"}  # the #1309 cleanup; only its undo lines are skipped
# Not scanned: the scanner, its tests, docs and rules that describe these patterns.
SKIP_PREFIXES = ("tests/", "docs/", ".agent/")
SKIP_FILES = {"scripts/home_write_scan.py"}
SKIP_SUFFIXES = (".md", ".png", ".jpg", ".ico", ".zip", ".whl", ".onnx", ".bin")


def scan_text(path: str, text: str) -> list[dict]:
    out = []
    for no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            continue
        for name, rx in RULES:
            if rx.search(line):
                if READ_ONLY.search(line):
                    continue
                if path in ALLOW_FILES and UNDO.search(line):
                    continue
                if name == "hooks-path" and re.search(r"--local\b|--worktree\b", line):
                    continue
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
    names = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True).stdout
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
    print(f"{len(found)} findings")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
