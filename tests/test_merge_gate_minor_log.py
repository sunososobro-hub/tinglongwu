"""#1947: merge-gate shows gh's own error text and a failed Minor-findings log never blocks the merge."""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import merge_gate  # noqa: E402

MINOR = {"id": "T1", "comment_id": 7, "path": "a.py", "url": "u", "severity": "minor", "text": "nit"}


def fake_run(calls, fail_on=None, stderr="gh: HTTP 403: Resource not accessible (HTTP 403)"):
    def run(cmd, **kw):
        calls.append(cmd)
        if fail_on and fail_on in " ".join(cmd):
            return SimpleNamespace(returncode=1, stdout="", stderr=stderr, args=cmd)
        return SimpleNamespace(returncode=0, stdout="{}", stderr="", args=cmd)
    return run


def test_gh_error_carries_stderr(monkeypatch):
    monkeypatch.setattr(merge_gate.subprocess, "run", fake_run([], fail_on="issues/9/comments"))
    with pytest.raises(subprocess.CalledProcessError) as ei:
        merge_gate._gh("repos/x/y/issues/9/comments")
    assert "HTTP 403" in str(ei.value)
    assert "issues/9/comments" in str(ei.value)


def test_log_failure_warns_and_still_replies_and_resolves(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(merge_gate.subprocess, "run", fake_run(calls, fail_on="issues/55/comments"))
    monkeypatch.setattr(merge_gate, "minor_bot_thread", lambda t: True)
    monkeypatch.setattr(merge_gate, "minor_log_issue", lambda: 55)
    merge_gate.resolve_minor(1925, [MINOR])
    err = capsys.readouterr().err
    assert "#1925 warn: minor log failed:" in err and "HTTP 403" in err
    joined = [" ".join(c) for c in calls]
    assert any("pulls/1925/comments/7/replies" in c for c in joined)
    assert any("resolveReviewThread" in c for c in joined)


def test_log_issue_lookup_failure_does_not_raise(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(merge_gate.subprocess, "run", fake_run(calls))
    monkeypatch.setattr(merge_gate, "minor_bot_thread", lambda t: True)

    def boom():
        raise subprocess.CalledProcessError(1, ["gh"], stderr="gh: HTTP 422")
    monkeypatch.setattr(merge_gate, "minor_log_issue", boom)
    merge_gate.resolve_minor(1862, [MINOR])
    assert "#1862 warn: minor log failed: gh: HTTP 422" in capsys.readouterr().err
    assert any("resolveReviewThread" in " ".join(c) for c in calls)
