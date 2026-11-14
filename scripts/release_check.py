"""Release check for the CUDA-free core zip (#612): no CUDA files inside, size <= 300 MB.

Usage: python scripts/release_check.py dist/tinglongwu-win64.zip [--max-mb 300]
       python scripts/release_check.py --job-start <unix seconds>   (#762: time used by the build job, vs the budget)
Both forms may be combined. Exit code 0 = ok, 1 = problem(s) (printed), 2 = zip missing.
The job time is reported, never a failure: a slow runner must not block a release (over budget -> a warning).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import zipfile
from pathlib import Path

MAX_CORE_MB = 300
CORE_JOB_BUDGET_MIN = 20  # #762: the release `windows` job (core zip build + checks) should end within this
BYTES_PER_MB = 1 << 20
SECONDS_PER_MIN = 60


def core_zip_problems(zip_path: Path, max_mb: float = MAX_CORE_MB) -> list[str]:
    """Problems that make `zip_path` not a valid core zip (empty list = fine)."""
    problems: list[str] = []
    size_mb = zip_path.stat().st_size / BYTES_PER_MB
    if size_mb > max_mb:
        problems.append(f"core zip is {size_mb:.1f} MB, over the {max_mb:g} MB budget")
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            parts = name.replace("\\", "/").lower().split("/")
            if "cuda_libs" in parts[:-1] or parts[-1] == "ggml-cuda.dll":
                problems.append(f"CUDA file in core zip: {name}")
    return problems


def job_time_line(start: float, now: float, budget_min: float = CORE_JOB_BUDGET_MIN) -> tuple[bool, str]:
    """(within budget, one line) for a job that started at `start` (unix seconds) and is at `now`."""
    minutes = max(0.0, now - start) / SECONDS_PER_MIN
    ok = minutes <= budget_min
    verdict = "within" if ok else "OVER"
    return ok, f"windows job time: {minutes:.1f} min ({verdict} the {budget_min:g} min budget)"


def summary(lines: list[str]) -> None:
    """Append to the GitHub job summary when there is one (the dry run's size / time are read from there)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path and lines:
        with open(path, "a", encoding="utf-8") as f:
            f.write("".join(f"- {ln}\n" for ln in lines))


def main(argv: list[str] | None = None, now: float | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zip", type=Path, nargs="?")
    ap.add_argument("--max-mb", type=float, default=MAX_CORE_MB)
    ap.add_argument("--job-start", type=float, help="unix seconds when the build job started: report its time")
    ap.add_argument("--job-budget-min", type=float, default=CORE_JOB_BUDGET_MIN)
    a = ap.parse_args(argv)
    if a.zip is None and a.job_start is None:
        ap.error("give the zip to check, --job-start, or both")
    out: list[str] = []
    problems: list[str] = []
    if a.zip is not None:
        if not a.zip.is_file():
            print(f"release_check: {a.zip} not found")
            return 2
        out.append(f"core zip size: {a.zip.stat().st_size / BYTES_PER_MB:.1f} MB (budget {a.max_mb:g} MB)")
        print(out[-1])
        problems = core_zip_problems(a.zip, a.max_mb)
        for p in problems:
            print("FAIL:", p)
        out += [f"FAIL: {p}" for p in problems]
    if a.job_start is not None:
        ok, line = job_time_line(a.job_start, time.time() if now is None else now, a.job_budget_min)
        out.append(line)
        print(line if ok else f"::warning::{line}")
    summary(out)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
