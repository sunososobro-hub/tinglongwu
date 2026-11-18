"""#1471: rules of the board scan Action."""
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from board_scan import (board_line, dev_red_since, dev_red_too_long, is_zombie_branch, needs_triage,
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
