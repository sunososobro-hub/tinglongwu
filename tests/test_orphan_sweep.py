"""#1470: decision function of the orphan-claim sweep (rules section 6)."""
from datetime import datetime, timedelta, UTC
import subprocess

import pytest
from unittest.mock import patch

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from orphan_sweep import _delete_at, _sweep_empty_claims, is_empty_claim, is_orphan, timeout_minutes

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def mins(n):
    return NOW - timedelta(minutes=n)


SONNET = {"status:doing", "model:sonnet"}
OPUS = {"status:doing", "model:opus"}


def test_timeouts():
    assert timeout_minutes(SONNET) == 20
    assert timeout_minutes(OPUS) == 45


def test_sonnet_boundary():
    assert not is_orphan(SONNET, mins(19), NOW, False)
    assert is_orphan(SONNET, mins(21), NOW, False)


def test_opus_boundary():
    assert not is_orphan(OPUS, mins(30), NOW, False)
    assert is_orphan(OPUS, mins(46), NOW, False)


def test_new_commit_on_open_pr_keeps_claim():
    assert not is_orphan(SONNET, mins(60), NOW, True, mins(5))


def test_open_pr_without_new_commit_is_orphan():
    assert is_orphan(SONNET, mins(60), NOW, True, mins(90))
    assert is_orphan(SONNET, mins(60), NOW, True, None)


def test_not_doing_is_never_orphan():
    assert not is_orphan({"status:todo", "model:sonnet"}, mins(600), NOW, False)


def test_empty_claim_deletes_stale_zero_ahead_branch():
    assert is_empty_claim("fix/1854", 0, False, {"status:todo"}, mins(10), NOW)
    assert is_empty_claim("fix/1854-r2", 0, False, set(), mins(6), NOW)
    assert is_empty_claim("fix/9999", 0, False, None, None, NOW)


def test_empty_claim_keeps_non_matching_branch_names():
    assert not is_empty_claim("dev", 0, False, {"status:todo"}, mins(60), NOW)
    assert not is_empty_claim("main", 0, False, {"status:todo"}, mins(60), NOW)
    assert not is_empty_claim("fix/1795-mt-context-default", 0, False, {"status:todo"}, mins(60), NOW)
    assert not is_empty_claim("wip/314-whard", 0, False, {"status:todo"}, mins(60), NOW)


def test_empty_claim_keeps_branch_with_commits_or_open_pr():
    assert not is_empty_claim("fix/1854", 1, False, {"status:todo"}, mins(60), NOW)
    assert not is_empty_claim("fix/1854", 0, True, {"status:todo"}, mins(60), NOW)


def test_empty_claim_keeps_doing_or_recently_updated_issue():
    assert not is_empty_claim("fix/1854", 0, False, {"status:doing"}, mins(60), NOW)
    assert not is_empty_claim("fix/1854", 0, False, {"status:todo"}, mins(5), NOW)
    assert not is_empty_claim("fix/1854", 0, False, {"status:todo"}, mins(2), NOW)


class FakeRepo:
    """In-memory GitHub for _sweep_empty_claims: refs, ahead counts per SHA, open PRs, issues."""

    def __init__(self, refs, ahead=None, prs=None, issue=None):
        self.refs = dict(refs)                  # branch -> sha
        self.ahead = dict(ahead or {})          # sha -> commits ahead of dev (default 0)
        self.prs = list(prs or [])              # open PRs (head dicts)
        self.issue = issue if issue is not None else {"labels": [], "updated_at": "2026-11-18T11:00:00Z"}
        self.deleted = []
        self.on_ref_read = None                 # hook: called with branch name on each ref read
        self.fail_delete = set()

    def gh_list(self, path):
        if "pulls?" in path:
            return [{"head": h} for h in self.prs]
        if "branches?" in path:
            return [{"name": n} for n in self.refs]
        return []

    def gh(self, path, *args):
        if "/git/ref/heads/" in path:
            name = path.split("/git/ref/heads/", 1)[1]
            if self.on_ref_read:
                self.on_ref_read(name)
            if name not in self.refs:
                raise subprocess.CalledProcessError(1, ["gh"], "", "gh: Not Found (HTTP 404)")
            return {"object": {"sha": self.refs[name]}}
        if "compare/dev..." in path:
            return {"ahead_by": self.ahead.get(path.split("compare/dev...", 1)[1], 0)}
        raise AssertionError(f"unexpected gh call {path} {args}")

    def delete_at(self, name, sha):
        if name in self.fail_delete or self.refs.get(name) != sha:
            raise subprocess.CalledProcessError(1, ["git"], "", "! [rejected] (stale info)")
        del self.refs[name]
        self.deleted.append(name)

    def run(self, apply=True):
        issue = lambda n: self.issue(n) if callable(self.issue) else self.issue
        with patch("orphan_sweep._gh_list", side_effect=self.gh_list), \
             patch("orphan_sweep._gh", side_effect=self.gh), \
             patch("orphan_sweep._issue_or_none", side_effect=issue), \
             patch("orphan_sweep._delete_at", side_effect=self.delete_at):
            _sweep_empty_claims(NOW, apply=apply)


OURS = {"repo": {"full_name": "sunososobro-hub/tinglongwu"}}


def test_sweep_deletes_only_empty_claims(capsys):
    repo = FakeRepo({"dev": "d0", "fix/10": "d0", "fix/20-r2": "c1", "fix/30": "d0", "feat/7": "d0"},
                    ahead={"c1": 1}, prs=[{"ref": "fix/30", **OURS}])
    repo.run()
    assert repo.deleted == ["fix/10"]
    assert "empty branch fix/10" in capsys.readouterr().out


def test_sweep_dry_run_prints_but_never_deletes(capsys):
    repo = FakeRepo({"fix/40": "d0"})
    repo.run(apply=False)
    assert repo.deleted == []
    assert "empty branch fix/40" in capsys.readouterr().out


def test_fork_pr_with_same_head_name_does_not_protect_the_branch():
    repo = FakeRepo({"fix/40": "d0"}, prs=[{"ref": "fix/40", "repo": {"full_name": "someone/tinglongwu"}}])
    repo.run()
    assert repo.deleted == ["fix/40"]


def test_commit_pushed_after_the_check_keeps_the_branch(capsys):
    repo = FakeRepo({"fix/50": "d0"}, ahead={"c9": 1})
    reads = []

    def push_after_recheck(name):
        reads.append(name)
        if len(reads) == 3:              # scan, re-check, then a worker pushes before the delete
            repo.refs[name] = "c9"
    repo.on_ref_read = push_after_recheck
    real_delete = repo.delete_at

    def delete_after_push(name, sha):
        push_after_recheck(name)
        real_delete(name, sha)
    with patch.object(repo, "delete_at", side_effect=delete_after_push):
        repo.run()
    assert repo.refs["fix/50"] == "c9" and repo.deleted == []
    assert "warning: skipped fix/50" in capsys.readouterr().out


def test_pr_opened_between_scan_and_delete_keeps_the_branch():
    repo = FakeRepo({"fix/60": "d0"})

    def open_pr(name):
        repo.prs.append({"ref": name, **OURS})
    repo.on_ref_read = open_pr
    repo.run()
    assert repo.deleted == []


def test_issue_claimed_between_scan_and_delete_keeps_the_branch():
    states = iter([{"labels": [{"name": "status:todo"}], "updated_at": "2026-11-18T11:00:00Z"},
                   {"labels": [{"name": "status:doing"}], "updated_at": "2026-11-18T11:59:30Z"}])
    repo = FakeRepo({"fix/70": "d0"}, issue=lambda n: next(states))
    repo.run()
    assert repo.deleted == []


def test_api_error_on_one_branch_does_not_stop_the_sweep(capsys):
    repo = FakeRepo({"fix/80": "d0", "fix/81": "d0"})
    repo.fail_delete = {"fix/80"}
    repo.run()
    assert repo.deleted == ["fix/81"]
    assert "warning: skipped fix/80" in capsys.readouterr().out


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def test_delete_at_is_refused_when_the_branch_moved(tmp_path):
    """Real git: the lease deletes at the checked SHA and refuses once the branch has moved."""
    bare, work = tmp_path / "origin.git", tmp_path / "work"
    _git("init", "-q", "--bare", str(bare), cwd=tmp_path)
    _git("init", "-q", str(work), cwd=tmp_path)
    for k, v in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        _git("config", k, v, cwd=work)
    _git("commit", "-q", "--allow-empty", "-m", "base", cwd=work)
    base = _git("rev-parse", "HEAD", cwd=work)
    _git("remote", "add", "origin", str(bare), cwd=work)
    _git("push", "-q", "origin", f"{base}:refs/heads/fix/1", f"{base}:refs/heads/fix/2", cwd=work)
    _git("commit", "-q", "--allow-empty", "-m", "work", cwd=work)
    moved = _git("rev-parse", "HEAD", cwd=work)
    _git("push", "-q", "-f", "origin", f"{moved}:refs/heads/fix/2", cwd=work)

    _delete_at("fix/1", base, cwd=str(work))
    with pytest.raises(subprocess.CalledProcessError):
        _delete_at("fix/2", base, cwd=str(work))
    heads = _git("ls-remote", "--heads", "origin", cwd=work)
    assert "refs/heads/fix/1" not in heads and f"{moved}\trefs/heads/fix/2" in heads
