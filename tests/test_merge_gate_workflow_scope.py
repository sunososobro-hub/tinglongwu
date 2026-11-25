"""#1948: a merge refused with 403 'without workflow scope' is a hold, not an error."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import merge_gate  # noqa: E402

SCOPE_ERR = ("gh: Refusing to allow a Personal Access Token to create or update workflow "
             "`.github/workflows/mobile-apk.yml` without `workflow` scope (HTTP 403)")
PR = {"base": {"ref": "dev"}, "head": {"sha": "abc"}, "body": "x", "labels": []}


def setup(monkeypatch, stderr, rc=1):
    posted = []

    def fake_gh(path, *args):
        if "comments" in path:
            posted.append(args)
            return {}
        return PR

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, rc, "", stderr)

    monkeypatch.setattr(merge_gate, "_gh", fake_gh)
    monkeypatch.setattr(merge_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(merge_gate, "legacy_freeze", lambda: False)
    return posted


def test_403_workflow_scope_is_hold(monkeypatch, capsys):
    posted = setup(monkeypatch, SCOPE_ERR)
    merge_gate.apply(7, PR, "merge", "ok", [], 0)
    out = capsys.readouterr().out
    assert f"#7 hold: {merge_gate.WORKFLOW_HOLD}" in out
    assert len(posted) == 1


def test_hold_comment_not_repeated(monkeypatch):
    posted = setup(monkeypatch, SCOPE_ERR)
    merge_gate.apply(7, PR, "merge", "ok", [{"body": "merge-gate: hold: earlier"}], 0)
    assert posted == []


def test_other_failure_still_raises(monkeypatch):
    setup(monkeypatch, "HTTP 405 not mergeable")
    try:
        merge_gate.apply(7, PR, "merge", "ok", [], 0)
    except subprocess.CalledProcessError:
        return
    raise AssertionError("expected error")
