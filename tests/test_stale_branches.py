"""#344: stale branch pruning, driven through a fake GitHub API (no network)."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import stale_branches as sb  # noqa: E402

R = "o/r"
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def _pr(n, ref, sha, state="closed", merged=False, repo=R, created="2026-11-10T00:00:00Z"):
    return {"number": n, "state": state, "merged_at": "2026-11-11T00:00:00Z" if merged else None,
            "created_at": created, "head": {"ref": ref, "sha": sha, "repo": {"full_name": repo}}}


class FakeGitHub:
    def __init__(self, branches, prs, ahead=None, dates=None, issues=None):
        self.branches = {b: s for b, s in branches}
        self.protected = {"locked"}
        self.prs, self.ahead, self.dates, self.issues = prs, ahead or {}, dates or {}, issues or {}
        self.deleted = []

    def __call__(self, method, path):
        if method == "DELETE":
            name = path.split("/git/refs/heads/", 1)[1]
            assert name in self.branches
            self.deleted.append(name)
            return None
        assert method == "GET"
        if path.startswith("/repos/o/r/branches"):
            page = int(path.rsplit("page=", 1)[1])
            items = [{"name": b, "commit": {"sha": s}, "protected": b in self.protected}
                     for b, s in self.branches.items()]
            return items[(page - 1) * 100:page * 100]
        if path.startswith("/repos/o/r/pulls"):
            page = int(path.rsplit("page=", 1)[1])
            return self.prs[(page - 1) * 100:page * 100]
        if path.startswith("/repos/o/r/compare/dev..."):
            return {"ahead_by": self.ahead.get(path.split("...", 1)[1], 0)}
        if path.startswith("/repos/o/r/commits/"):
            sha = path.rsplit("/", 1)[1]
            return {"commit": {"committer": {"date": self.dates.get(sha, "2026-11-01T00:00:00Z")}}}
        if path.startswith("/repos/o/r/issues/"):
            return self.issues.get(int(path.rsplit("/", 1)[1]), {"state": "closed", "labels": []})
        raise AssertionError(path)


def _gh():
    return FakeGitHub(
        branches=[("main", "m"), ("dev", "d"), ("gh-pages", "g"), ("locked", "l"),
                  ("fix/1", "a1"),       # merged PR, tip unchanged -> delete
                  ("fix/2", "a2"),       # closed PR, open retry PR on the same branch -> keep
                  ("fix/3", "new3"),     # closed PR, pushed to after closing -> keep
                  ("fix/4", "a4"),       # lock branch, issue open + status:doing -> keep
                  ("fix/5", "a5"),       # no PR, nothing beyond dev, old -> delete
                  ("fix/6", "a6"),       # no PR, nothing beyond dev, fresh -> keep
                  ("feature", "a7"),     # no PR, has own commits -> keep
                  ("keepme", "a8")],     # allowlisted -> keep
        prs=[_pr(10, "fix/1", "a1", merged=True),
             _pr(11, "fix/2", "old2"), _pr(12, "fix/2", "a2", state="open", created="2026-11-12T00:00:00Z"),
             _pr(13, "fix/3", "old3"),
             _pr(14, "main", "m", repo="someone/fork"),
             _pr(15, "keepme", "a8")],
        ahead={"feature": 3},
        dates={"a6": "2026-11-12T00:00:00Z"},
        issues={4: {"state": "open", "labels": [{"name": "status:doing"}]}},
    )


def test_plan_deletes_only_stale_branches():
    names = [n for n, _, _ in sb.plan(_gh(), R, keep=["keepme"], now=NOW)]
    assert sorted(names) == ["fix/1", "fix/5"]


def test_dry_run_lists_but_deletes_nothing():
    gh = _gh()
    lines = sb.run(gh, R, dry_run=True, keep=["keepme"], now=NOW)
    assert gh.deleted == []
    assert len(lines) == 2 and all(ln.startswith("would delete") for ln in lines)
    assert any("fix/1" in ln and "PR #10 merged" in ln for ln in lines)


def test_real_run_deletes_and_reports_sha():
    gh = _gh()
    lines = sb.run(gh, R, dry_run=False, keep=["keepme"], now=NOW)
    assert sorted(gh.deleted) == ["fix/1", "fix/5"]
    assert any(ln.startswith("deleted `fix/5` a5") for ln in lines)


def test_main_writes_job_summary(tmp_path, monkeypatch):
    gh = _gh()
    monkeypatch.setattr(sb, "http_api", gh)
    out = tmp_path / "summary.md"
    assert sb.main(["--repo", R, "--dry-run", "--keep", "keepme", "--summary", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "stale branches (dry run):" in text and "fix/1" in text and gh.deleted == []


def test_lock_issue_parsing():
    assert sb._lock_issue("fix/344") == 344 and sb._lock_issue("fix/12-r2") == 12
    assert sb._lock_issue("claude/x") is None and sb._lock_issue("fix/12-slug") is None
