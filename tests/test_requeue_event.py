"""#1886 (C5 of #1875): event-driven requeue rules and the event -> requeue path."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import board_scan  # noqa: E402
import requeue_event as rq  # noqa: E402
from requeue_event import ci_why, dirty_why, review_why  # noqa: E402

REPO = "sunososobro-hub/tinglongwu"
MAJOR = '_⚠️ Potential issue_ | <picture><img alt="Major"></picture> Major** · logic'
MINOR = '_⚠️ Potential issue_ | <picture><img alt="Minor"></picture> Minor** · wording'


def pr(num=5, ref="fix/12", sha="abc", state="open", base="dev", repo=REPO, mergeable="clean",
       created="2026-11-25T01:00:00Z"):
    return {"number": num, "state": state, "base": {"ref": base}, "mergeable_state": mergeable,
            "head": {"ref": ref, "sha": sha, "repo": {"full_name": repo}}, "created_at": created}


def review(login="coderabbitai[bot]", sha="abc", at="2026-11-25T01:08:00Z", body=""):
    return {"user": {"login": login}, "commit_id": sha, "submitted_at": at, "body": body}


# dev push -> dirty
def test_dirty_after_push():
    assert dirty_why(pr(mergeable="dirty")) == "dirty"


def test_clean_or_fork_or_other_base_is_not_requeued():
    assert dirty_why(pr(mergeable="clean")) is None
    assert dirty_why(pr(mergeable="dirty", repo="someone/fork")) is None
    assert dirty_why(pr(mergeable="dirty", base="main")) is None
    assert dirty_why(pr(mergeable="dirty", state="closed")) is None


# ci completed -> test red on the current head
def test_ci_red_on_current_head():
    assert ci_why(pr(sha="abc"), "abc", "failure") == "test red"
    assert ci_why(pr(sha="abc"), "abc", "timed_out") == "test red"


def test_ci_green_or_old_head_is_ignored():
    assert ci_why(pr(sha="abc"), "abc", "success") is None
    assert ci_why(pr(sha="abc"), "abc", None) is None  # `test` not finished: ci failed on another job
    assert ci_why(pr(sha="new"), "abc", "failure") is None  # a newer push is already running


# review bot Critical / Major
def test_major_review_after_author_window():
    assert review_why(pr(), review(), [MAJOR]) == "review Critical/Major"
    assert review_why(pr(), review(body="**Critical** " + MAJOR.replace("Major", "Critical")), []) == \
        "review Critical/Major"


def test_minor_only_human_old_head_or_inside_window_are_ignored():
    assert review_why(pr(), review(), [MINOR]) is None
    assert review_why(pr(), review(login="someone"), [MAJOR]) is None
    assert review_why(pr(), review(sha="old"), [MAJOR]) is None
    # 4 min after open: the author worker is still in its single `sleep 270` and handles it itself
    assert review_why(pr(), review(at="2026-11-25T01:04:00Z"), [MAJOR]) is None


# event -> board_scan requeue (I/O faked)
class FakeGH:
    def __init__(self, prs, issue_labels, test="failure"):
        self.prs, self.labels, self.test, self.writes = prs, issue_labels, test, []

    def gh(self, path, *args):
        if "-X" in args:
            self.writes.append((path, args))
            return {}
        if path.startswith(f"repos/{REPO}/pulls/") and path.count("/") == 4:
            return self.prs[int(path.rsplit("/", 1)[1])]
        if path.startswith(f"repos/{REPO}/issues/"):
            return {"state": "open", "labels": [{"name": n} for n in self.labels]}
        raise AssertionError(path)

    def gh_list(self, path):
        if "/pulls?state=open" in path:
            return list(self.prs.values())
        return []  # no earlier requeue comments


@pytest.fixture
def fake(monkeypatch):
    def make(prs, labels, test="failure"):
        f = FakeGH(prs, labels, test)
        monkeypatch.setattr(board_scan, "_gh", f.gh)
        monkeypatch.setattr(board_scan, "_gh_list", f.gh_list)
        monkeypatch.setattr(board_scan, "_issue_or_none", lambda n: f.gh(f"repos/{REPO}/issues/{n}"))
        monkeypatch.setattr(board_scan, "_test_conclusion", lambda sha: f.test)
        return f
    return make


def test_push_requeues_dirty_pr_issue(fake):
    f = fake({5: pr(mergeable="dirty"), 6: pr(num=6, ref="fix/13")}, {"status:review"})
    out = rq.on_push(apply=True)
    assert out == ["PR #5 dirty: requeued #12"]
    paths = [p for p, _ in f.writes]
    assert f"repos/{REPO}/issues/12/labels/status:review" in paths
    assert any("requeue: PR #5 is dirty" in " ".join(a) for _, a in f.writes)


def test_ci_red_requeues_but_doing_issue_is_left_alone(fake):
    f = fake({5: pr()}, {"status:review"})
    assert rq.on_ci([5], "abc", apply=True) == ["PR #5 test red: requeued #12"]
    f = fake({5: pr()}, {"status:doing"})
    assert rq.on_ci([5], "abc", apply=True) == ["PR #5 test red: no change (issue not in status:review)"]
    assert f.writes == []


def test_dry_run_writes_nothing(fake):
    f = fake({5: pr(mergeable="dirty")}, {"status:review"})
    assert rq.on_push(apply=False) == ["PR #5 dirty (dry run)"]
    assert f.writes == []
