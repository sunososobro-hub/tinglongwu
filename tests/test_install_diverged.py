"""#383: installer update handles a diverged/rewritten clone instead of silently staying on stale code."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash") or not shutil.which("git"),
                                reason="needs bash and git")
ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"}


def _git(*a, cwd):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True, env=ENV).stdout.strip()


def _setup(tmp_path):
    up = tmp_path / "up"; up.mkdir()
    _git("init", "-q", "-b", "dev", cwd=up)
    (up / "a").write_text("1"); _git("add", "a", cwd=up); _git("commit", "-qm", "1", cwd=up)
    clone = tmp_path / "c"
    _git("clone", "-q", str(up), str(clone), cwd=tmp_path)
    (clone / "a").write_text("local"); _git("commit", "-qam", "local", cwd=clone)   # old history
    _git("commit", "-q", "--amend", "-m", "x", "--date=2000-01-01T00:00:00", cwd=up)  # upstream rewrote history
    _git("fetch", "-q", "origin", cwd=clone)
    return up, clone


def _run(clone):
    code = ('TLWU_SOURCE_ONLY=1 source "$S"; DIR="$C"; REF=dev; L=en; update_branch')
    return subprocess.run(["bash", "-c", code], capture_output=True, text=True,
                          env={**ENV, "HOME": str(clone.parent), "S": str(ROOT / "scripts" / "install.sh"), "C": str(clone)})


def test_diverged_clean_backs_up_and_resets(tmp_path):
    up, clone = _setup(tmp_path)
    old_head = _git("rev-parse", "HEAD", cwd=clone)
    r = _run(clone)
    assert r.returncode == 0, r.stderr
    assert _git("rev-parse", "HEAD", cwd=clone) == _git("rev-parse", "origin/dev", cwd=clone)
    backups = _git("branch", "--list", "backup-*", cwd=clone).split()
    assert len(backups) == 1 and _git("rev-parse", backups[0], cwd=clone) == old_head
    assert "diverged" in r.stdout and backups[0] in r.stdout
    assert _git("rev-parse", "--short", "HEAD", cwd=clone) in r.stdout


def test_diverged_dirty_stops(tmp_path):
    up, clone = _setup(tmp_path)
    (clone / "a").write_text("dirty")
    old_head = _git("rev-parse", "HEAD", cwd=clone)
    r = _run(clone)
    assert r.returncode != 0
    assert "local changes" in r.stderr
    assert _git("rev-parse", "HEAD", cwd=clone) == old_head
    assert _git("branch", "--list", "backup-*", cwd=clone) == ""


def test_fast_forward_prints_sha(tmp_path):
    up = tmp_path / "up"; up.mkdir()
    _git("init", "-q", "-b", "dev", cwd=up)
    (up / "a").write_text("1"); _git("add", "a", cwd=up); _git("commit", "-qm", "1", cwd=up)
    clone = tmp_path / "c"; _git("clone", "-q", str(up), str(clone), cwd=tmp_path)
    (up / "a").write_text("2"); _git("commit", "-qam", "2", cwd=up)
    _git("fetch", "-q", "origin", cwd=clone)
    r = _run(clone)
    assert r.returncode == 0, r.stderr
    assert _git("rev-parse", "--short", "HEAD", cwd=clone) in r.stdout
    assert _git("branch", "--list", "backup-*", cwd=clone) == ""
