"""#1857 part 3: CI runs the suite once, split over parallel jobs with TLWU_SHARD=k/n (tests/conftest.py).
All shards together must run every collected test exactly once."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import parse_shard, shard_of_files

ROOT = Path(__file__).resolve().parent.parent
FILES = ["tests/test_ci_scope.py", "tests/test_commit_identity_944.py", "tests/test_ci_parallel_1857.py",
         "tests/test_merge_gate.py"]


def test_parse_shard():
    assert parse_shard("") is None and parse_shard(None) is None
    assert parse_shard("2/4") == (2, 4) and parse_shard(" 1 / 1 ") == (1, 1)
    for bad in ("0/4", "5/4", "4", "a/b", "1/0"):
        with pytest.raises(ValueError):
            parse_shard(bad)


def test_every_file_in_exactly_one_shard_and_balanced():
    counts = {f"t{i}.py": c for i, c in enumerate([50, 40, 30, 20, 10, 10, 5, 5, 3, 1])}
    tk = {"t1.py", "t4.py", "t7.py"}
    where = shard_of_files(counts, 3, is_tk=lambda f: f in tk)
    assert set(where) == set(counts) and set(where.values()) <= {1, 2, 3}
    # the real-Tk files land on different shards (each shard runs them one after another)
    assert len({where[f] for f in tk}) == 3
    load = [sum(c for f, c in counts.items() if where[f] == k) for k in (1, 2, 3)]
    assert max(load) - min(load) <= 50  # no shard is more than the largest file above another
    assert shard_of_files(counts, 3, is_tk=lambda f: f in tk) == where  # deterministic


def _collected(shard):
    env = {**os.environ, "TLWU_SHARD": shard} if shard else {k: v for k, v in os.environ.items() if k != "TLWU_SHARD"}
    r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *FILES],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    return [line for line in r.stdout.splitlines() if "::" in line]


def test_shards_partition_the_real_collection():
    full = _collected("")
    parts = [_collected(f"{k}/3") for k in (1, 2, 3)]
    joined = [t for p in parts for t in p]
    assert sorted(joined) == sorted(full) and len(set(joined)) == len(joined)
    assert all(parts)  # 4 files over 3 shards: none is empty


def test_bad_shard_value_fails_the_run():
    r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", FILES[0]],
                       cwd=ROOT, env={**os.environ, "TLWU_SHARD": "5/4"}, capture_output=True, text=True, timeout=120)
    assert r.returncode != 0 and "TLWU_SHARD" in r.stdout + r.stderr
