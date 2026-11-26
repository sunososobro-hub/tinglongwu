"""#1733: a merged dev PR closes only the issue its head names, and only with a closing keyword."""
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "close_on_merge", Path(__file__).resolve().parents[1] / "scripts" / "close_on_merge.py")
com = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(com)


def test_closes_matching_head():
    assert com.issue_to_close("Closes #1733\n\ntlwu-usage: minutes=3", "fix/1733") == 1733


def test_keyword_variants_and_retry_head():
    for word in ("close", "closed", "Fixes", "fixed", "fix", "resolve", "Resolves", "resolved", "closes:"):
        assert com.issue_to_close(f"{word} #42", "fix/42-r3") == 42, word
    assert com.issue_to_close("Closes #7", "feat/7") == 7


def test_refs_never_closes():
    assert com.issue_to_close("Refs #1733", "fix/1733") is None


def test_number_must_match_head():
    # a stray number in the body (e.g. the throttle issue) must not be closed
    assert com.issue_to_close("Closes #574", "fix/1733") is None
    assert com.issue_to_close("Closes #17330", "fix/1733") is None


def test_other_heads_do_nothing():
    for head in ("release/v0.5.3", "hotfix/v0.5.4", "docs/fastlane-9", "fix/abc", "fix/12-x", "", None):
        assert com.issue_to_close("Closes #12", head) is None, head


def test_empty_body():
    assert com.issue_to_close(None, "fix/12") is None
    assert com.issue_to_close("", "fix/12") is None


def test_close_issue_skips_closed(monkeypatch):
    calls = []

    def fake_api(path, token, method="GET", data=None):
        calls.append((method, path))
        return {"state": "closed", "labels": []}

    monkeypatch.setattr(com, "_api", fake_api)
    assert com.close_issue("o/r", 5, 9, "t") == "already-closed"
    assert calls == [("GET", "/repos/o/r/issues/5")]


def test_close_issue_swaps_labels_and_closes(monkeypatch):
    calls = []

    def fake_api(path, token, method="GET", data=None):
        calls.append((method, path, data))
        if method == "GET":
            return {"state": "open", "labels": [{"name": "status:review"}, {"name": "P1"}]}
        return None

    monkeypatch.setattr(com, "_api", fake_api)
    assert com.close_issue("o/r", 5, 9, "t") == "closed"
    methods = [(m, p) for m, p, _ in calls]
    assert ("DELETE", "/repos/o/r/issues/5/labels/status%3Areview") in methods
    assert ("POST", "/repos/o/r/issues/5/labels") in methods
    assert calls[-1] == ("PATCH", "/repos/o/r/issues/5", {"state": "closed", "state_reason": "completed"})


# --- #1853: a merged `Refs #N` PR puts the issue back in the queue ---

def test_partial_matches_head_only():
    assert com.issue_to_requeue("Refs #1853\n\ntlwu-usage: minutes=3", "fix/1853") == 1853
    assert com.issue_to_requeue("refs: #42", "fix/42-r2") == 42
    assert com.issue_to_requeue("Refs #7", "feat/7") == 7
    assert com.issue_to_requeue("Refs #574", "fix/1853") is None      # a stray number is not this PR's issue
    assert com.issue_to_requeue("Refs #18530", "fix/1853") is None
    assert com.issue_to_requeue("Refs #12", "release/v0.5.3") is None
    assert com.issue_to_requeue(None, "fix/12") is None


def test_closing_keyword_wins_over_refs():
    assert com.issue_to_requeue("Closes #12\nRefs #12", "fix/12") is None
    assert com.issue_to_close("Closes #12\nRefs #12", "fix/12") == 12


def _fake(monkeypatch, issue):
    calls = []

    def fake_api(path, token, method="GET", data=None):
        calls.append((method, path, data))
        if method == "GET":
            return [] if path.endswith("/comments?per_page=100") else issue
        return None

    monkeypatch.setattr(com, "_api", fake_api)
    return calls


def test_partial_goes_back_to_todo(monkeypatch):
    calls = _fake(monkeypatch, {"state": "open", "labels": [{"name": "status:review"}, {"name": "P0"}]})
    assert com.release_partial("o/r", 5, 9, "t") == "status:todo"
    assert ("DELETE", "/repos/o/r/issues/5/labels/status%3Areview", None) in calls
    assert ("POST", "/repos/o/r/issues/5/labels", {"labels": ["status:todo"]}) in calls
    comment = next(c for c in calls if c[0] == "POST" and c[1].endswith("/comments"))
    assert "#9 merged" in comment[2]["body"]
    assert not any(m == "PATCH" for m, _, _ in calls)  # never closes


def test_partial_hands_on_issue_waits_for_the_test(monkeypatch):
    calls = _fake(monkeypatch, {"state": "open", "labels": [{"name": "status:review"}, {"name": "需實測"}]})
    assert com.release_partial("o/r", 5, 9, "t") == "status:needs-test"
    assert ("POST", "/repos/o/r/issues/5/labels", {"labels": ["status:needs-test"]}) in calls


def test_partial_parked_issue_gets_no_status(monkeypatch):
    calls = _fake(monkeypatch, {"state": "open", "labels": [{"name": "status:doing"}, {"name": "status:frozen"}]})
    assert com.release_partial("o/r", 5, 9, "t") == "parked"
    assert [m for m, _, _ in calls] == ["GET", "DELETE"]  # no status added, no comment


def test_partial_leaves_unclaimed_closed_and_pr_alone(monkeypatch):
    for issue, want in (({"state": "open", "labels": [{"name": "status:todo"}]}, "no-claim"),
                        ({"state": "closed", "labels": [{"name": "status:review"}]}, "already-closed"),
                        ({"state": "open", "pull_request": {"url": "u"}, "labels": [{"name": "status:review"}]}, "is-pr")):
        calls = _fake(monkeypatch, issue)
        assert com.release_partial("o/r", 5, 9, "t") == want
        assert [m for m, _, _ in calls] == ["GET"]


def test_release_is_resumable_after_a_failed_call(monkeypatch):
    """Review #1855: a failure after the claim label is gone must not leave the issue stuck with no status."""
    state = {"labels": ["status:review", "P1"], "comments": [], "fail_next_comment": True}

    def fake_api(path, token, method="GET", data=None):
        if method == "GET" and path.endswith("/comments?per_page=100"):
            return [{"body": b} for b in state["comments"]]
        if method == "GET":
            return {"state": "open", "labels": [{"name": n} for n in state["labels"]]}
        if method == "POST" and path.endswith("/labels"):
            state["labels"] += data["labels"]
        elif method == "POST" and path.endswith("/comments"):
            if state["fail_next_comment"]:
                state["fail_next_comment"] = False
                raise OSError("boom")
            state["comments"].append(data["body"])
        elif method == "DELETE":
            state["labels"].remove(path.rsplit("/", 1)[1].replace("%3A", ":"))
        return None

    monkeypatch.setattr(com, "_api", fake_api)
    try:
        com.release_partial("o/r", 5, 9, "t")
    except OSError:
        pass
    assert "status:review" in state["labels"]  # claim kept, so the rerun is not "no-claim"
    assert com.release_partial("o/r", 5, 9, "t") == "status:todo"
    assert sorted(state["labels"]) == ["P1", "status:todo"]
    assert len(state["comments"]) == 1
    assert com.release_partial("o/r", 5, 9, "t") == "no-claim"
    assert len(state["comments"]) == 1
