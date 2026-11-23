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
