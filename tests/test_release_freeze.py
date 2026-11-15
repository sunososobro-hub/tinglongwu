"""#953: release freeze decision (pure functions, no network)."""
import importlib.util
import pathlib

_p = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "release_freeze.py"
_s = importlib.util.spec_from_file_location("release_freeze", _p)
rf = importlib.util.module_from_spec(_s)
_s.loader.exec_module(rf)


def test_not_frozen_without_release_pr():
    assert rf.is_frozen("0.3.6", [], ["v0.3.5"]) is False
    assert rf.is_frozen("0.3.6", ["fix #1: something"], []) is False


def test_stale_release_pr_when_tag_exists():
    assert rf.is_frozen("0.3.6", ["release: v0.3.6"], ["v0.3.6", "v0.3.5"]) is False


def test_release_pr_for_other_version_does_not_freeze():
    assert rf.is_frozen("0.3.6", ["release: v0.3.5"], []) is False


def test_frozen():
    assert rf.is_frozen("0.3.6", ["release: v0.3.6"], ["v0.3.5"]) is True


def test_blocks_merge_unless_release_fix():
    assert rf.blocks_merge(True, "Closes #1") is True
    assert rf.blocks_merge(True, None) is True
    assert rf.blocks_merge(True, "release-fix\nCloses #1") is False
    assert rf.blocks_merge(False, "Closes #1") is False


def test_976_only_a_legacy_dev_head_release_pr_freezes_dev():
    repo = "o/r"
    snap = {"title": "release: v0.3.2", "head": {"ref": "release/v0.3.2", "repo": {"full_name": repo}}}
    legacy = {"title": "release: v0.3.2", "head": {"ref": "dev", "repo": {"full_name": repo}}}
    fork = {"title": "release: v0.3.2", "head": {"ref": "dev", "repo": {"full_name": "x/fork"}}}
    assert rf.legacy_release_titles([snap, fork], repo) == []
    assert not rf.is_frozen("0.3.2", rf.legacy_release_titles([snap, fork], repo), [])
    assert rf.is_frozen("0.3.2", rf.legacy_release_titles([snap, legacy], repo), [])
