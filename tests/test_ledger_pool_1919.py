"""#1919: the ledger comment carries one ticket-supply line `ready=<n> startable=<n> blocked_by_dep=<n> ...`."""
import json
import sys
from datetime import datetime, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ledger  # noqa: E402

FIX = json.loads((Path(__file__).parent / "fixtures" / "ledger_pool_1919.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 25, 11, 0, tzinfo=UTC)


def test_pool_counts_from_fixture():
    # ready: 10, 12, 13, 14, 16 (15 needs-owner, 17 no ok-to-work, 11 blocked, 20 is a PR)
    # startable: 10, 13 (dep #99 not open), 16 (mid-line mention is not a dependency); 12 waits on #10, 14 has PR #20
    # blocked_by_dep: 11 and 12 wait on open #10
    assert ledger.pool_counts(FIX["issues"], FIX["open_prs"]) == {"ready": 5, "startable": 3, "blocked_by_dep": 2}


def test_workers_ran_counts_roles_and_contributors():
    # W-hard, S-easy A (two spellings), S-easy B; R-hard / split-check are not workers; old and foreign lines ignored;
    # plus contributor-1 (once); contributor-2 too old; the producer account is already counted by role
    assert ledger.workers_ran(FIX["comments"], FIX["recent_prs"], NOW) == 4


def test_worker_key_spellings():
    assert ledger.worker_key("S-easy slot A: x") == ledger.worker_key("S-easy-A: x") == ledger.worker_key("S-easy A: x") == "S-easy A"
    assert ledger.worker_key("S-easy: x") == "S-easy A"
    assert ledger.worker_key("slot C: x") == "S-easy C"
    assert ledger.worker_key("W-hard: x") == "W-hard" and ledger.worker_key("S-hard: x") == "S-hard"
    assert ledger.worker_key("R-hard: x") is None and ledger.worker_key("S-split: x") is None


def test_pool_line_in_dry_run(monkeypatch, capsys):
    def gh_list(path):
        if path.startswith(f"repos/{ledger.REPO}/issues?state=open"):
            return FIX["issues"]
        if "pulls?state=open" in path:
            return FIX["open_prs"]
        if f"issues/{ledger.LOG_ISSUE}/comments" in path and "T" in path and "since=" in path:
            return FIX["comments"] if "2026-11-24T11" in path else []
        return []

    monkeypatch.setattr(ledger, "_gh_list", gh_list)
    monkeypatch.setattr(ledger, "_gh", lambda path, *a: FIX["recent_prs"] if "pulls?state=all" in path
                        else {"body": "level: 2", "labels": []})

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(ledger, "datetime", _DT)
    monkeypatch.setattr(sys, "argv", ["ledger.py", "--dry-run"])
    assert ledger.main() == 0
    out = capsys.readouterr().out
    assert "ready=5 startable=3 blocked_by_dep=2 workers_24h=4 target=8" in out.splitlines()
