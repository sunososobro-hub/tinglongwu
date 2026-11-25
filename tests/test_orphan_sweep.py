"""#1470: decision function of the orphan-claim sweep (rules section 6)."""
from datetime import datetime, timedelta, UTC
import subprocess
from unittest.mock import patch

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from orphan_sweep import _sweep_empty_claims, is_empty_claim, is_orphan, timeout_minutes

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


def test_sweep_empty_claims_apply_rechecks_and_continues_on_delete_error(capsys):
    stale_ts = mins(30).isoformat().replace("+00:00", "Z")
    branches = [{"name": "dev"}, {"name": "fix/10"}, {"name": "fix/20-r2"}, {"name": "fix/30"}]
    compare_calls = {"fix/10": 0, "fix/20-r2": 0, "fix/30": 0}
    deleted = []

    def fake_gh_list(path):
        if "pulls?" in path:
            return [{"head": {"ref": "fix/30", "repo": {"full_name": "sunososobro-hub/tinglongwu"}}}]
        if "branches?" in path:
            return branches
        return []

    def fake_gh(path, *args):
        if "compare/dev..." in path:
            br = path.split("compare/dev...", 1)[1]
            compare_calls[br] += 1
            # fix/20-r2 gains a commit right before delete
            return {"ahead_by": 1 if br == "fix/20-r2" and compare_calls[br] == 2 else 0}
        if "git/refs/heads/" in path and args == ("-X", "DELETE"):
            br = path.split("git/refs/heads/", 1)[1]
            if br == "fix/10":
                raise subprocess.CalledProcessError(1, ["gh"], "", "HTTP 422")
            deleted.append(br)
            return None
        return None

    with patch("orphan_sweep._gh_list", side_effect=fake_gh_list), \
         patch("orphan_sweep._gh", side_effect=fake_gh), \
         patch("orphan_sweep._issue_or_none", return_value={"labels": [], "updated_at": stale_ts}):
        _sweep_empty_claims(NOW, apply=True)

    out = capsys.readouterr().out
    assert "empty branch fix/10" in out
    assert "warning: skipped fix/10" in out
    assert "empty branch fix/20-r2" in out
    assert "fix/30" not in out
    assert deleted == []


def _fake_env(issues, compare=None):
    """Patches for _sweep_empty_claims: `issues` is a list of issue dicts returned in order."""
    deleted = []

    def fake_gh_list(path):
        if "pulls?" in path:
            # a fork PR with the same head name does not protect our branch
            return [{"head": {"ref": "fix/40", "repo": {"full_name": "someone/tinglongwu"}}}]
        if "branches?" in path:
            return [{"name": "fix/40"}]
        return []

    def fake_gh(path, *args):
        if "compare/dev..." in path:
            if compare is not None:
                return compare(path)
            return {"ahead_by": 0}
        if args == ("-X", "DELETE"):
            deleted.append(path.split("git/refs/heads/", 1)[1])
        return None

    it = iter(issues)
    return deleted, [patch("orphan_sweep._gh_list", side_effect=fake_gh_list),
                     patch("orphan_sweep._gh", side_effect=fake_gh),
                     patch("orphan_sweep._issue_or_none", side_effect=lambda n: next(it))]


def _run(patches, apply):
    for p in patches:
        p.start()
    try:
        _sweep_empty_claims(NOW, apply=apply)
    finally:
        for p in patches:
            p.stop()


def test_sweep_deletes_empty_branch_even_if_a_fork_pr_uses_the_name(capsys):
    stale = {"labels": [{"name": "status:todo"}], "updated_at": "2026-11-18T11:00:00Z"}
    deleted, patches = _fake_env([stale, stale])
    _run(patches, apply=True)
    assert deleted == ["fix/40"]
    assert "empty branch fix/40" in capsys.readouterr().out


def test_sweep_dry_run_prints_but_never_deletes(capsys):
    stale = {"labels": [], "updated_at": "2026-11-18T11:00:00Z"}
    deleted, patches = _fake_env([stale])
    _run(patches, apply=False)
    assert deleted == []
    assert "empty branch fix/40" in capsys.readouterr().out


def test_sweep_keeps_branch_claimed_between_scan_and_delete():
    stale = {"labels": [{"name": "status:todo"}], "updated_at": "2026-11-18T11:00:00Z"}
    claimed = {"labels": [{"name": "status:doing"}], "updated_at": "2026-11-18T11:59:30Z"}
    deleted, patches = _fake_env([stale, claimed])
    _run(patches, apply=True)
    assert deleted == []


def test_sweep_compare_error_skips_branch_without_crashing(capsys):
    def boom(path):
        raise subprocess.CalledProcessError(1, ["gh"], "", "gh: Not Found (HTTP 404)")
    deleted, patches = _fake_env([], compare=boom)
    _run(patches, apply=True)
    assert deleted == []
    assert "warning: skipped fix/40" in capsys.readouterr().out
