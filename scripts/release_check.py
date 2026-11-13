"""Release check for the CUDA-free core zip (#612): no CUDA files inside, size <= 300 MB.

Usage: python scripts/release_check.py dist/tinglongwu-win64.zip [--max-mb 300]
Exit code 0 = ok, 1 = problem(s) (printed), 2 = zip missing.
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

MAX_CORE_MB = 300


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
    a = ap.parse_args(argv)
    if not a.zip.is_file():
        print(f"release_check: {a.zip} not found")
        return 2
    print(f"core zip size: {a.zip.stat().st_size / (1 << 20):.1f} MB (budget {a.max_mb:g} MB)")
    problems = core_zip_problems(a.zip, a.max_mb)
    for p in problems:
        print("FAIL:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
