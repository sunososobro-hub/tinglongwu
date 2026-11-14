"""Release check for the CUDA-free core zip (#612): no CUDA files inside, size <= 300 MB.

Usage: python scripts/release_check.py dist/tinglongwu-win64.zip [--max-mb 300] [--ttfs ttfs.json]
--ttfs prints install-start -> first-subtitle time (non-blocking: never changes the exit code).
Exit code 0 = ok, 1 = problem(s) (printed), 2 = zip missing.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import zipfile
from pathlib import Path

MAX_CORE_MB = 300
TTFS_BUDGET_S = 180


def ttfs_lines(path: Path | None) -> list[str]:
    """Report lines for a ttfs.json (#776). Informational only: never raises, never fails the check."""
    if path is None:
        return []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("ttfs_seconds") is None:
            return ["ttfs: unknown (ttfs_seconds is null)"]
        excl = float(data["excl_download_seconds"])
        if not math.isfinite(excl):
            return ["ttfs: unknown (excl_download_seconds is not finite)"]
        label = f"{excl:.0f} s excl. download ({data.get('os', '?')}, {data.get('version', '?')})"
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as e:
        return [f"ttfs: unknown ({type(e).__name__}: {e})"]
    lines = [f"ttfs: {label}"]
    if excl > TTFS_BUDGET_S:
        lines.append(f"WARN ttfs {excl:.0f} s is over the {TTFS_BUDGET_S} s budget")
    return lines


def core_zip_problems(zip_path: Path, max_mb: float = MAX_CORE_MB) -> list[str]:
    """Problems that make `zip_path` not a valid core zip (empty list = fine)."""
    problems: list[str] = []
    size_mb = zip_path.stat().st_size / (1 << 20)
    if size_mb > max_mb:
        problems.append(f"core zip is {size_mb:.1f} MB, over the {max_mb:g} MB budget")
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            parts = name.replace("\\", "/").lower().split("/")
            if "cuda_libs" in parts[:-1] or parts[-1] == "ggml-cuda.dll":
                problems.append(f"CUDA file in core zip: {name}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("zip", type=Path)
    ap.add_argument("--max-mb", type=float, default=MAX_CORE_MB)
    ap.add_argument("--ttfs", type=Path, default=None, help="ttfs.json: print time to first subtitle (non-blocking)")
    a = ap.parse_args(argv)
    if not a.zip.is_file():
        print(f"release_check: {a.zip} not found")
        return 2
    print(f"core zip size: {a.zip.stat().st_size / (1 << 20):.1f} MB (budget {a.max_mb:g} MB)")
    problems = core_zip_problems(a.zip, a.max_mb)
    for p in problems:
        print("FAIL:", p)
    for line in ttfs_lines(a.ttfs):
        print(line)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
