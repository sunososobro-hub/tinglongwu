"""Behaviour tests for scripts/shipped_notice.py selection logic (#1504)."""
import importlib.util
import pathlib
import subprocess

spec = importlib.util.spec_from_file_location("shipped_notice", pathlib.Path(__file__).parent.parent / "scripts" / "shipped_notice.py")
sn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sn)


def _git(cwd, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *a], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout.strip()


def test_is_ancestor(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "a")
    fix = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "tag", "v0.1.0")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "b")
    late = _git(tmp_path, "rev-parse", "HEAD")
    assert sn.is_ancestor(fix, "v0.1.0", cwd=str(tmp_path))
    assert not sn.is_ancestor(late, "v0.1.0", cwd=str(tmp_path))


def test_issues_from_body():
    assert sn.issues_from_body("Closes #5\nOwner comments: none\nRefs #7, closes #5") == [5, 7]
    assert sn.issues_from_body(None) == []


def test_decide_prerelease_vs_final_and_dedup():
    assert sn.decide("v1.0.0-rc1", True, True, set()) == "Shipped in v1.0.0-rc1 (pre-release)"
    assert sn.decide("v1.0.0-rc2", True, True, {"pre"}) is None
    assert sn.decide("v1.0.0", False, True, {"pre"}) == "Shipped in v1.0.0"
    assert sn.decide("v1.0.1", False, True, {"final"}) is None
    assert sn.decide("v1.0.0", False, False, set()) is None


def test_shipped_kinds_parses_existing_comments():
    got = sn.shipped_kinds(["hello", "Shipped in v0.4.7 (pre-release)", "Shipped in v0.4.8"])
    assert got == {"pre", "final"}


def test_is_ancestor_reports_git_errors(tmp_path):
    import pytest
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "a")
    with pytest.raises(RuntimeError):
        sn.is_ancestor("HEAD", "no-such-tag", cwd=str(tmp_path))


def test_is_ancestor_missing_commit_is_not_contained(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "a")
    _git(tmp_path, "tag", "v0.1.0")
    assert sn.is_ancestor("0" * 40, "v0.1.0", cwd=str(tmp_path)) is False
