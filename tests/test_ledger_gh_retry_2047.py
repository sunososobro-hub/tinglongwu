"""#2047: one failed gh call is retried once; a lookup that still fails skips that commit instead of aborting."""
import json
import subprocess
import sys
from datetime import datetime, UTC
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ledger  # noqa: E402

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)


def _ok(out):
    return subprocess.CompletedProcess([], 0, stdout=json.dumps(out), stderr="")


def _fail(cmd):
    return subprocess.CalledProcessError(1, cmd, stderr="HTTP 502")


def test_gh_retries_once_then_succeeds(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if len(calls) == 1:
            raise _fail(cmd)
        return _ok({"a": 1})

    monkeypatch.setattr(ledger.subprocess, "run", run)
    monkeypatch.setattr(ledger.time, "sleep", lambda s: None)
    assert ledger._gh("repos/x") == {"a": 1}
    assert len(calls) == 2


def test_gh_second_failure_prints_command_and_stderr(monkeypatch, capsys):
    def run(cmd, **kw):
        raise _fail(cmd)

    monkeypatch.setattr(ledger.subprocess, "run", run)
    monkeypatch.setattr(ledger.time, "sleep", lambda s: None)
    with pytest.raises(subprocess.CalledProcessError):
        ledger._gh("repos/x")
    err = capsys.readouterr().err
    assert "gh api repos/x" in err and "HTTP 502" in err


def test_collect_models_skips_commit_whose_check_runs_keep_failing(monkeypatch):
    merged = "2026-11-27T10:00:00Z"
    prs = [{"number": n, "merged_at": merged, "body": "tlwu-usage: model=sonnet tool_calls=3 minutes=5",
            "head": {"ref": f"fix/{n}"}} for n in (1, 2)]

    def run(cmd, **kw):
        path = cmd[2]
        if "check-runs" in path and "sha2" in path:
            raise _fail(cmd)  # fails every time, retry included
        if "/pulls?state=closed" in path:
            return _ok([prs])
        if "/commits?per_page" in path:
            n = path.split("/pulls/")[1].split("/")[0]
            return _ok([[{"sha": f"sha{n}"}]])
        if "check-runs" in path:
            return _ok({"check_runs": [{"conclusion": "success"}]})
        return _ok([[]])

    monkeypatch.setattr(ledger.subprocess, "run", run)
    monkeypatch.setattr(ledger.time, "sleep", lambda s: None)
    rows = ledger.collect_models(NOW)
    assert len(rows) == 2
    assert [r["red"] for r in rows] == [0, 0]
