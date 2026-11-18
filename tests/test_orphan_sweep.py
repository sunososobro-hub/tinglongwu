"""#1470: decision function of the orphan-claim sweep (rules section 6)."""
from datetime import datetime, timedelta, UTC

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from orphan_sweep import is_orphan, timeout_minutes

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
