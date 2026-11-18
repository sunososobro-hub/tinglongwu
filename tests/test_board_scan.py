"""#1471: rules of the board scan Action."""
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from board_scan import (board_line, run_state, zombie_issue_labels, dev_red_since, dev_red_too_long, is_zombie_branch, needs_triage,
                        pr_needs_action)

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)  # 17:00 TPE


def ago(m):
    return NOW - timedelta(minutes=m)


# PR waiting > 30 min
def test_pr_recent_is_not_flagged():
    assert pr_needs_action(NOW, ago(29), "failure", "dirty", {"needs-opus"}) is None


def test_pr_red_test():
    assert pr_needs_action(NOW, ago(31), "failure", "clean", set()) == "test red"


def test_pr_dirty():
    assert pr_needs_action(NOW, ago(45), "success", "dirty", set()) == "dirty"


def test_pr_needs_opus():
    assert pr_needs_action(NOW, ago(60), "success", "clean", {"needs-opus"}) == "needs-opus"


def test_pr_green_clean_is_ok():
    assert pr_needs_action(NOW, ago(120), "success", "clean", set()) is None
    assert pr_needs_action(NOW, ago(120), None, "unstable", set()) is None


# zombie fix/N branch
def test_zombie_branch():
    assert is_zombie_branch("fix/12", 0, False, {"status:todo"})
    assert is_zombie_branch("fix/12-r2", 0, False, set())


def test_not_zombie():
    assert not is_zombie_branch("fix/12", 1, False, set())          # has work
    assert not is_zombie_branch("fix/12", 0, True, set())           # had a PR (open, closed or merged leftover)
    assert not is_zombie_branch("fix/12", 0, False, {"status:doing"})  # live claim
    assert not is_zombie_branch("feat/12", 0, False, set())
    assert not is_zombie_branch("release/v1.2.3", 0, False, set())
    assert not is_zombie_branch("fix/12", 0, False, None)           # issue closed: stale-branch cleanup


# needs-triage
def test_needs_triage():
    assert needs_triage({"needs-triage", "P2"})
    assert not needs_triage({"status:todo"})


# dev red > 2 h
def test_dev_green_tip():
    assert dev_red_since([(ago(5), "success"), (ago(200), "failure")]) is None


def test_dev_red_streak_start():
    hist = [(ago(10), "failure"), (ago(50), None), (ago(130), "failure"), (ago(300), "success")]
    assert dev_red_since(hist) == ago(130)
    assert dev_red_too_long(NOW, hist)


def test_dev_red_short():
    hist = [(ago(10), "failure"), (ago(90), "failure"), (ago(150), "success")]
    assert not dev_red_too_long(NOW, hist)


def test_dev_pending_tip_uses_last_finished():
    assert dev_red_since([(ago(1), None), (ago(5), "success")]) is None


# output line
def test_board_line():
    assert board_line(NOW, []) == "[board 17:00] ok"
    assert board_line(NOW, ["PR #5 dirty", "triage #9"]) == "[board 17:00] act: PR #5 dirty; triage #9"


def test_zombie_issue_labels():
    assert zombie_issue_labels({"state": "open", "labels": []}) == set()
    assert zombie_issue_labels({"state": "open", "labels": [{"name": "P2"}]}) == {"P2"}
    assert zombie_issue_labels({"state": "closed", "labels": []}) is None
    assert zombie_issue_labels({"state": "open", "labels": [], "pull_request": {}}) is None  # #N is a PR
    assert zombie_issue_labels(None) is None


def test_pr_mergeable_unknown_is_not_ok():
    assert pr_needs_action(NOW, ago(40), "success", "unknown", set()) == "mergeable unknown"
    assert pr_needs_action(NOW, ago(40), "success", None, set()) == "mergeable unknown"
    assert pr_needs_action(NOW, ago(10), "success", "unknown", set()) is None  # recent: not waiting yet


def test_dev_red_timed_by_failing_run_not_commit():
    # commit from 3 h ago whose test only finished red 5 min ago: red for 5 min, not 3 h
    assert not dev_red_too_long(NOW, [(ago(5), "failure"), (ago(200), "success")])


def _run(started, completed, concl, status="completed"):
    return {"started_at": started.isoformat(), "completed_at": completed.isoformat(), "conclusion": concl,
            "status": status}


def test_state_uses_run_time_not_commit_date():
    # a commit made hours ago whose only test run failed 5 min ago: red since 5 min ago
    assert run_state([_run(ago(15), ago(5), "failure")]) == ("failure", ago(5))


def test_state_failed_rerun_keeps_first_failure():
    runs = [_run(ago(160), ago(150), "failure"), _run(ago(15), ago(5), "failure")]
    assert run_state(runs) == ("failure", ago(150))


def test_state_green_attempt_ends_red():
    runs = [_run(ago(300), ago(290), "failure"), _run(ago(200), ago(190), "success"),
            _run(ago(15), ago(5), "failure")]
    assert run_state(runs) == ("failure", ago(5))
    assert run_state(runs[:2]) == ("success", None)
    assert run_state([_run(ago(2), ago(1), None, "in_progress")]) == (None, None)


def test_dev_red_since_earliest_failure_in_streak():
    # tip failed at -150, parent finished red later at -5, older commit green: streak is 150 min
    hist = [(ago(150), "failure"), (ago(5), "failure"), (ago(400), "success"), (ago(500), "failure")]
    assert dev_red_since(hist) == ago(150)
