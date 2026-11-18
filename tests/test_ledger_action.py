"""#1469: ledger share/deficit math, mix parsing, zero-total case, and the CLI dry-run (stubbed gh)."""
import os
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ledger  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_share_and_deficit():
    rows = {r["type"]: r for r in ledger.build_rows({"bug": 25, "enhancement": 75}, ledger.DEFAULT_MIX)}
    assert rows["bug"]["share"] == 25.0 and rows["bug"]["deficit"] == 3.0
    assert rows["enhancement"]["share"] == 75.0 and rows["enhancement"]["deficit"] == -25.0
    assert rows["QA"]["deficit"] == 15.0


def test_zero_total_share_zero_deficit_is_target():
    for r in ledger.build_rows({}, ledger.DEFAULT_MIX):
        assert r["share"] == 0.0 and r["deficit"] == r["target"]
    assert ledger.infra_line(0, 0) == "infra: 0 / 0 = 0.0% (cap 10%)"


def test_infra_line():
    assert ledger.infra_line(10, 200) == "infra: 10 / 200 = 5.0% (cap 10%)"


def test_mix_parse_valid_and_invalid():
    body = "level: 2\nfocus: x\nmix: bug 30, enhancement 40, eval 10, documentation 10, maint 10"
    assert ledger.parse_mix(body) == ({"bug": 30, "enhancement": 40, "eval": 10, "documentation": 10, "maint": 10}, True)
    for bad in ["level: 2", "a\nb\nmix: bug 50, enhancement 60", "a\nb\nmix: bug 28, enhancement 50, eval 11, documentation 5, maint 5",
                "a\nb\nmix: bug x, enhancement 50, eval 11, documentation 5, maint 6"]:
        assert ledger.parse_mix(bad) == (ledger.DEFAULT_MIX, False)


def test_usage_minutes_last_line():
    assert ledger.usage_minutes("x\ntlwu-usage: model=sonnet tool_calls=3 minutes=12") == 12
    assert ledger.usage_minutes("no usage") is None


def test_cli_dry_run_with_stub_gh(tmp_path):
    stub = tmp_path / "gh"
    stub.write_text("#!/usr/bin/env python3\nimport sys, json\nu = sys.argv[2]\n"
                    "print(json.dumps({'body': 'level: 2\\nfocus: x\\nmix: bad'}) if u.endswith('/issues/574') else '[]')\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "ledger.py"), "--dry-run"],
                         capture_output=True, text=True, env=env, check=True).stdout
    assert "| bug | 0 | 0.0% | 28% | +28.0 |" in out
    assert "mix invalid, default used" in out and "infra: 0 / 0 = 0.0% (cap 10%)" in out


def test_mix_unicode_digit_and_huge_value_fall_back():
    assert ledger.parse_mix("a\nb\nmix: bug \u00b2, enhancement 50, eval 11, documentation 5, maint 6") == (ledger.DEFAULT_MIX, False)
    assert ledger.parse_mix("a\nb\nmix: bug " + "9" * 5000 + ", enhancement 50") == (ledger.DEFAULT_MIX, False)


def test_usage_minutes_ignores_absurd_value():
    assert ledger.usage_minutes("tlwu-usage: model=x minutes=" + "9" * 5000) is None


def test_linked_issue_prefers_head_branch():
    assert ledger.linked_issue("fix/1469", "Refs #5\nCloses #1469") == 1469
    assert ledger.linked_issue("fix/12-r2", None) == 12
    assert ledger.linked_issue("hotfix/v1.2.3", "Closes #7") == 7
    assert ledger.linked_issue("docs/x", "none") is None


def test_final_usage_only_last_line():
    assert ledger.final_usage("tlwu-usage: model=x minutes=99\nsome prose") is None
    assert ledger.final_usage("Closes #1\n\ntlwu-usage: model=sonnet tool_calls=3 minutes=12\n") == 12


def test_collect_counts_only_producer_comments_and_7d_infra_denominator(monkeypatch):
    from datetime import datetime, timedelta, UTC
    now = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    iso = lambda d: (now - d).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    pr = {"merged_at": iso(timedelta(days=3)), "head": {"ref": "fix/9"},
          "body": "Closes #9\ntlwu-usage: model=sonnet tool_calls=1 minutes=20"}
    comments = [{"user": {"login": ledger.PRODUCER}, "created_at": iso(timedelta(days=3)), "body": "tlwu-usage: kind=qa minutes=30"},
                {"user": {"login": "outsider"}, "created_at": iso(timedelta(hours=1)), "body": "tlwu-usage: minutes=500"},
                {"user": {"login": ledger.PRODUCER}, "created_at": iso(timedelta(hours=1)), "body": "tlwu-usage: kind=qa minutes=10"}]

    def fake_list(path):
        return [pr] if "/pulls" in path else comments if "/issues/61/" in path else []
    monkeypatch.setattr(ledger, "_gh_list", fake_list)
    monkeypatch.setattr(ledger, "_gh", lambda path, *a: {"labels": [{"name": "infra"}, {"name": "maint"}]})
    by_type, untyped, infra_min, total7 = ledger.collect(now)
    assert by_type == {"QA": 10} and untyped == 0  # the 3-day-old PR/comment are outside the 48 h table
    assert infra_min == 20 and total7 == 20 + 30 + 10  # 7-day denominator includes producer QA minutes, not the outsider's


def test_usage_rejects_partial_minutes_value():
    assert ledger.usage_minutes("tlwu-usage: model=x minutes=12.5") is None
    assert ledger.usage_minutes("tlwu-usage: model=x minutes=12") == 12


def test_deleted_linked_issue_keeps_minutes_in_total(monkeypatch):
    from datetime import datetime, timedelta, UTC
    now = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    pr = {"merged_at": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), "head": {"ref": "fix/9"},
          "body": "Closes #9\ntlwu-usage: model=sonnet tool_calls=1 minutes=20"}
    monkeypatch.setattr(ledger, "_gh_list", lambda path: [pr] if "/pulls" in path else [])

    def gone(path, *a):
        raise subprocess.CalledProcessError(1, "gh", stderr="gh: Not Found (HTTP 404)")
    monkeypatch.setattr(ledger, "_gh", gone)
    by_type, untyped, infra_min, total7 = ledger.collect(now)
    assert untyped == 20 and total7 == 20 and infra_min == 0


def test_fix_minutes_only_phase_fix_lines():  # #1581
    assert ledger.fix_minutes("x\ntlwu-usage: model=sonnet minutes=7 phase=fix") == 7
    assert ledger.fix_minutes("tlwu-usage: model=sonnet minutes=7") is None
    assert ledger.fix_minutes("tlwu-usage: minutes=3 phase=fix\ntlwu-usage: minutes=9 phase=fix") == 9


def test_collect_fix_counts_producer_fix_comments_on_prs(monkeypatch):  # #1581
    from datetime import datetime, timedelta, UTC
    now = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    iso = lambda d: (now - d).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    url = "https://github.com/sunososobro-hub/tinglongwu/pull/{}#issuecomment-1".format
    comments = [
        {"user": {"login": ledger.PRODUCER}, "created_at": iso(timedelta(hours=2)), "html_url": url(50),
         "body": "CodeRabbit round fixed\ntlwu-usage: model=sonnet minutes=15 phase=fix"},
        {"user": {"login": ledger.PRODUCER}, "created_at": iso(timedelta(days=3)), "html_url": url(50),
         "body": "tlwu-usage: model=sonnet minutes=5 phase=fix"},
        {"user": {"login": "outsider"}, "created_at": iso(timedelta(hours=1)), "html_url": url(50),
         "body": "tlwu-usage: minutes=999 phase=fix"},
        {"user": {"login": ledger.PRODUCER}, "created_at": iso(timedelta(hours=1)),
         "html_url": "https://github.com/sunososobro-hub/tinglongwu/issues/61#issuecomment-2",
         "body": "tlwu-usage: kind=qa minutes=30 phase=fix"},  # not a PR: QA path owns #61
        {"user": {"login": ledger.PRODUCER}, "created_at": iso(timedelta(hours=1)), "html_url": url(51),
         "body": "tlwu-usage: model=sonnet minutes=40"},  # no phase=fix
    ]
    monkeypatch.setattr(ledger, "_gh_list", lambda path: comments if "/issues/comments" in path else [])
    monkeypatch.setattr(ledger, "_gh", lambda path, *a: {"labels": [{"name": "bug"}, {"name": "infra"}]})
    by_type, untyped, infra_min, total7, fix48 = ledger.collect_fix(now, pr_issue=lambda n: 9)
    assert by_type == {"bug": 15} and untyped == 0 and fix48 == 15
    assert total7 == 20 and infra_min == 20
    assert ledger.fix_line(15, 60) == "open->merge fix minutes (48 h): 15 (25.0% of total)"
