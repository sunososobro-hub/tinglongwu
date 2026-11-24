"""#1676: per-model quality metrics (review-fail rate, CI red rounds, post-merge incidents) with fixture PR data."""
import sys
from datetime import datetime, timedelta, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ledger  # noqa: E402

T0 = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def test_pr_model_from_last_line():
    assert ledger.pr_model("x\ntlwu-usage: model=opus tool_calls=3 minutes=12") == "opus"
    assert ledger.pr_model("tlwu-usage: model=sonnet minutes=1\nmore text") == "unknown"
    assert ledger.pr_model(None) == "unknown"


def test_review_fail_detection():
    assert ledger.has_review_fail(["ok", "AI review: FAIL - tests grep source"])
    assert ledger.has_review_fail(["Opus review: CHANGES"])
    assert not ledger.has_review_fail(["Opus review: PASS abc", "looks good"])


def test_red_rounds_counts_commits_with_failed_test():
    assert ledger.red_rounds({"a": ["failure"], "b": ["success"], "c": ["failure", "success"], "d": []}) == 2


def test_incident_by_revert_and_by_bug_issue():
    bug = {"number": 90, "created_at": T0 + timedelta(days=2), "body": "regression from #50", "labels": ["bug"]}
    assert ledger.is_incident(50, 40, T0, [bug], [])
    old = dict(bug, created_at=T0 + timedelta(days=8))
    assert not ledger.is_incident(50, 40, T0, [old], [])
    not_bug = dict(bug, labels=["enhancement"])
    assert not ledger.is_incident(50, 40, T0, [not_bug], [])
    via_issue = dict(bug, body="Refs #40 again")
    assert ledger.is_incident(50, 40, T0, [via_issue], [])
    own = dict(bug, number=40)
    assert not ledger.is_incident(50, 40, T0, [own], [])
    assert ledger.is_incident(50, 40, T0, [], [{"number": 77, "title": "Revert \"fix #40\"", "body": "Reverts #50"}])
    assert not ledger.is_incident(50, 40, T0, [], [{"number": 77, "title": "fix #1", "body": "mentions #50"}])
    assert not ledger.is_incident(5, None, T0, [dict(bug, body="see #50")], [])


def test_model_rows_and_render():
    prs = [
        {"model": "sonnet", "review_fail": True, "red": 2, "incident": False},
        {"model": "sonnet", "review_fail": False, "red": 0, "incident": True},
        {"model": "sonnet", "review_fail": False, "red": 1, "incident": False},
        {"model": "sonnet", "review_fail": False, "red": 1, "incident": False},
        {"model": "opus", "review_fail": False, "red": 0, "incident": False},
    ]
    rows = ledger.model_rows(prs)
    assert rows[0] == {"model": "sonnet", "prs": 4, "fail_rate": 25.0, "red_avg": 1.0, "incidents": 1}
    assert rows[1] == {"model": "opus", "prs": 1, "fail_rate": 0.0, "red_avg": 0.0, "incidents": 0}
    text = ledger.render_models(rows)
    assert "| model | PRs merged | review-fail rate | avg CI red rounds | post-merge incidents |" in text
    assert "| sonnet | 4 | 25.0% | 1.0 | 1 |" in text
    assert "no merged PRs" in ledger.render_models([])
