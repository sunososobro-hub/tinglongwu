"""#1993: a failed `gh api` call in mobile_runner_fallback shows gh's stderr and exits non-zero (no bare traceback)."""
import importlib.util
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "mobile_runner_fallback.py"
_spec = importlib.util.spec_from_file_location("mrf_1993", SCRIPT)
mrf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mrf)


def _env(monkeypatch):
    for k, v in {"REPO": "o/r", "RUN_ID": "1", "REF": "fix/1", "WAIT_MIN": "0"}.items():
        monkeypatch.setenv(k, v)


def test_dispatch_failure_prints_gh_stderr(monkeypatch, capsys):
    _env(monkeypatch)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if any("/jobs" in c for c in cmd):
            return subprocess.CompletedProcess(cmd, 0, stdout='{"jobs": []}', stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="HTTP 422: Unexpected inputs")

    monkeypatch.setattr(mrf.subprocess, "run", fake_run)
    monkeypatch.setattr(mrf.time, "sleep", lambda s: None)
    rc = mrf.main(["x", "watch"])
    err = capsys.readouterr().err
    assert rc != 0
    assert "HTTP 422: Unexpected inputs" in err
    assert "dispatches" in err
    assert not any("cancel" in " ".join(c) for c in calls)  # run is not cancelled when dispatch failed
