"""#1882 (C1 of #1875): scripts/claim_branch.sh against a local bare repo.

Two workers that read the same dev tip claim the same ticket: exactly one succeeds (#1865 double claim)."""
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "claim_branch.sh"

pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash") or not shutil.which("git"),
                                reason="bash script, cloud Linux workers only")


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def remote(tmp_path):
    """A bare `origin` with one dev commit, and two worker clones that both read the same dev tip."""
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    seed = tmp_path / "seed"
    _git(tmp_path, "init", "-q", "-b", "dev", str(seed))
    for k, v in (("user.name", "t"), ("user.email", "t@example.com")):
        _git(seed, "config", k, v)
    (seed / "a.txt").write_text("a")
    _git(seed, "add", "a.txt")
    _git(seed, "commit", "-q", "-m", "dev")
    _git(seed, "push", "-q", str(bare), "dev")
    workers = []
    for name in ("w1", "w2"):
        w = tmp_path / name
        _git(tmp_path, "clone", "-q", "-b", "dev", str(bare), str(w))
        for k, v in (("user.name", name), ("user.email", f"{name}@example.com")):
            _git(w, "config", k, v)
        workers.append(w)
    tip = _git(workers[0], "rev-parse", "origin/dev")
    assert tip == _git(workers[1], "rev-parse", "origin/dev")
    return bare, workers, tip


def _claim(worker, branch, base, issue="7", session=None, env=None):
    return subprocess.run(["bash", str(SCRIPT), branch, base, issue, session or worker.name],
                          cwd=worker, capture_output=True, text=True, env=env)


def test_two_workers_same_tip_exactly_one_claims(remote):
    bare, (w1, w2), tip = remote
    r1, r2 = _claim(w1, "fix/7", tip), _claim(w2, "fix/7", tip)
    assert [r1.returncode, r2.returncode] == [0, 1]
    claim = r1.stdout.strip()
    assert _git(bare, "rev-parse", "refs/heads/fix/7") == claim
    # the claim commit is empty, sits on the dev tip and names the ticket
    assert _git(bare, "rev-parse", f"{claim}^") == tip
    assert _git(bare, "rev-parse", f"{claim}^{{tree}}") == _git(bare, "rev-parse", f"{tip}^{{tree}}")
    assert _git(bare, "log", "-1", "--format=%s", claim).startswith("claim #7 w1 ")


def test_same_session_twice_still_one_winner(remote):
    """Same session, same identity, same timestamps: only the random nonce keeps the two claim commits different."""
    import os
    bare, (w1, w2), tip = remote
    env = dict(os.environ, GIT_AUTHOR_NAME="a", GIT_AUTHOR_EMAIL="a@example.com", GIT_COMMITTER_NAME="a",
               GIT_COMMITTER_EMAIL="a@example.com", GIT_AUTHOR_DATE="2026-11-25T00:00:00Z",
               GIT_COMMITTER_DATE="2026-11-25T00:00:00Z")
    rs = [_claim(w, "fix/7", tip, session="same", env=env) for w in (w1, w2)]
    assert sorted(r.returncode for r in rs) == [0, 1]


def test_parallel_claims_exactly_one_wins(remote):
    bare, (w1, w2), tip = remote
    results = {}
    go = threading.Barrier(2)

    def run(w):
        go.wait()
        results[w.name] = _claim(w, "fix/7", tip).returncode

    ts = [threading.Thread(target=run, args=(w,)) for w in (w1, w2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(results.values()) == [0, 1]


def test_existing_branch_on_dev_commit_is_not_fast_forwarded(remote):
    """An old-style claim (branch = a dev sha) is still a lock: the new claim would fast-forward it, but the
    empty lease refuses any push to an existing branch."""
    bare, (w1, w2), tip = remote
    _git(w1, "push", "-q", "origin", f"{tip}:refs/heads/fix/7")
    assert _claim(w2, "fix/7", tip).returncode == 1
    assert _git(bare, "rev-parse", "refs/heads/fix/7") == tip


def test_fix_round_claim_branch_per_head(remote):
    """A fix round on an open PR claims claim/N-<head sha7>; a later round on a new head is a new name."""
    bare, (w1, w2), tip = remote
    name = f"claim/7-{tip[:7]}"
    assert [_claim(w1, name, tip).returncode, _claim(w2, name, tip).returncode] == [0, 1]


def test_push_failure_is_not_contention(remote, tmp_path):
    """An unreachable origin (auth / network) exits 3 with git's message, not 1 (taken), so the worker reports
    the blocked write instead of skipping every item."""
    bare, (w1, _), tip = remote
    _git(w1, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    r = _claim(w1, "fix/7", tip)
    assert r.returncode == 3 and "push failed" in r.stderr and "taken" not in r.stderr


def test_usage_error(tmp_path):
    r = subprocess.run(["bash", str(SCRIPT), "fix/7"], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 2 and "usage" in r.stderr
