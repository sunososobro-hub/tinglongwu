"""#1918: per-folder OWNERS files (area, worker model, review level, protection) read by the merge gate and triage.

An `OWNERS` file is a tiny YAML subset: one `key: value` per line, `#` comments, flow lists `[a, "b/*.txt"]`,
`true` / `false`. Keys:

  area             default `area:*` label for work under this folder (`none`: the issue decides)
  model            `sonnet` or `opus`: the worker model for issues about this folder
  review           `none`, `bot` or `opus`: the review a PR touching this folder needs
  protected        `true` -> every file under this folder is a protected / g4 path (rules section 14)
  protected_files  globs relative to this folder (`*` stays inside one folder, `**` crosses folders) for single
                   protected files inside an otherwise open folder

Inheritance: a folder with no OWNERS (or a key missing from it) takes the nearest ancestor's value. Protection is
the union along the chain: a file is protected when ANY ancestor OWNERS says `protected: true` or lists it, so a
new nested OWNERS can never unprotect a path. Every `OWNERS` file is itself protected. The gate reads the OWNERS
files of its own checkout (dev), never the PR's, so a PR cannot loosen its own gate.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
KEYS = ("area", "model", "review", "protected", "protected_files")
MODELS = ("sonnet", "opus")
REVIEWS = ("none", "bot", "opus")


def parse(text: str) -> dict:
    """Parse the OWNERS YAML subset; unknown keys or bad values raise ValueError (fail closed)."""
    out: dict = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split(" #", 1)[0].strip() if not raw.lstrip().startswith("#") else ""
        if not line:
            continue
        key, sep, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if not sep or key not in KEYS:
            raise ValueError(f"OWNERS line {n}: unknown key {key!r}")
        if val.startswith("[") and val.endswith("]"):
            out[key] = [v.strip().strip("\"'") for v in val[1:-1].split(",") if v.strip()]
        elif val in ("true", "false"):
            out[key] = val == "true"
        else:
            out[key] = val.strip("\"'")
    if "model" in out and out["model"] not in MODELS:
        raise ValueError(f"OWNERS model must be one of {MODELS}, got {out['model']!r}")
    if "review" in out and out["review"] not in REVIEWS:
        raise ValueError(f"OWNERS review must be one of {REVIEWS}, got {out['review']!r}")
    if not isinstance(out.get("protected", False), bool):
        raise ValueError("OWNERS protected must be true or false")
    if not isinstance(out.get("protected_files", []), list):
        raise ValueError("OWNERS protected_files must be a list")
    return out


def _glob_re(pattern: str) -> re.Pattern:
    parts = re.split(r"(\*\*/?|\*|\?)", pattern)
    rx = "".join({"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*", "?": "[^/]"}.get(p, re.escape(p)) for p in parts)
    return re.compile(rx + r"\Z")


@lru_cache(maxsize=None)
def _load(root: str, folder: str) -> dict | None:
    f = Path(root, folder, "OWNERS")
    return parse(f.read_text(encoding="utf-8")) if f.is_file() else None


def chain(path: str, root: Path | str = ROOT) -> list[tuple[str, dict]]:
    """(folder, owners) pairs from the repo root down to the folder holding `path`; folders without OWNERS skipped."""
    root = str(root)
    if _load(root, "") is None:
        raise FileNotFoundError(f"no root OWNERS file in {root} (#1918): refusing to guess protected paths")
    parts = PurePosixPath(path).parts[:-1]
    folders = [""] + ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    return [(d, o) for d in folders if (o := _load(root, d)) is not None]


def owners_for(path: str, root: Path | str = ROOT) -> dict:
    """Effective area / model / review / protected for `path`: nearest OWNERS wins per key."""
    eff: dict = {}
    for _, o in chain(path, root):
        eff.update({k: v for k, v in o.items() if k != "protected_files"})
    eff["protected"] = is_protected(path, root)
    if eff["protected"]:
        eff["review"] = "opus"
    return eff


def is_protected(path: str, root: Path | str = ROOT) -> bool:
    """Rules section 14 protected / g4 path per the OWNERS chain. Files under tests/ never count (section 3)."""
    if not path:
        return False
    if PurePosixPath(path).name == "OWNERS":
        return True
    if path.startswith("tests/"):
        return False
    for folder, o in chain(path, root):
        if o.get("protected"):
            return True
        rel = path[len(folder) + 1:] if folder else path
        if any(_glob_re(p).match(rel) for p in o.get("protected_files", [])):
            return True
    return False
