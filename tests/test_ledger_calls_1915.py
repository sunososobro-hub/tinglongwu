"""#1915: the ledger weighs PRs by tool_calls (not wall-clock minutes) and drops absurd minutes as bad data."""
import sys
from datetime import datetime, timedelta, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ledger  # noqa: E402

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
ISO = (NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pr(n, usage):
    return {"merged_at": ISO, "head": {"ref": f"fix/{n}"}, "body": f"Closes #{n}\n\n{usage}"}


def _run(monkeypatch, prs):
    ledger.DROPPED.clear()
    ledger.MODEL_CALLS.clear()
    monkeypatch.setattr(ledger, "_gh_list", lambda path: prs if "/pulls" in path else [])
    monkeypatch.setattr(ledger, "_gh", lambda path, *a: {"labels": [{"name": "bug"}]})
    return ledger.collect(NOW)


def test_usage_weight_prefers_calls_and_drops_bad_minutes():
    assert ledger.usage_weight("tlwu-usage: model=sonnet tool_calls=30 minutes=135") == 30
    assert ledger.usage_weight("tlwu-usage: kind=qa minutes=10") == 10  # legacy line without calls
    assert ledger.usage_weight("tlwu-usage: model=sonnet tool_calls=30 minutes=30000000") is None
    assert ledger.usage_weight("tlwu-usage: model=sonnet tool_calls=30 minutes=601") is None
    assert ledger.usage_weight("tlwu-usage: model=sonnet tool_calls=30 minutes=600") == 30
    assert ledger.usage_weight("no usage line") is None


def test_collect_135min_30calls_counts_30_and_30m_minutes_dropped(monkeypatch):
    prs = [_pr(1, "tlwu-usage: model=sonnet tool_calls=30 minutes=135"),
           _pr(2, "tlwu-usage: model=opus tool_calls=40 minutes=30000000")]
    by_type, untyped, _infra, total7 = _run(monkeypatch, prs)
    assert by_type == {"bug": 30} and total7 == 30 and untyped == 0
    assert len(ledger.DROPPED) == 1 and "dropped 1 usage line" in ledger.warning_line()
    assert ledger.model_calls_line() == "by model: sonnet 30 opus 0"


def test_by_model_line_sums_calls_per_model(monkeypatch):
    prs = [_pr(1, "tlwu-usage: model=sonnet tool_calls=20 minutes=5"),
           _pr(2, "tlwu-usage: model=opus tool_calls=25 minutes=9"),
           _pr(3, "tlwu-usage: model=sonnet tool_calls=7 minutes=2")]
    _run(monkeypatch, prs)
    assert ledger.model_calls_line() == "by model: sonnet 27 opus 25"
    assert ledger.warning_line() == ""


def test_render_headers_say_calls():
    rows = ledger.build_rows({"bug": 30}, ledger.DEFAULT_MIX)
    text = ledger.render(rows, "infra: 0 / 0 = 0.0% (cap 10%)", 30, 0, True, NOW)
    assert "| type | calls | share | target | deficit |" in text and "total 30 calls" in text


def test_tally_model_uses_same_line_as_weight_1923():
    """#1923: earlier `model=opus` line + final legacy line without model -> counted as `unknown`, not `opus`."""
    ledger.MODEL_CALLS.clear()
    body = "tlwu-usage: model=opus tool_calls=9 minutes=5\nnotes\ntlwu-usage: minutes=7"
    assert ledger.usage_weight(body) == 7
    ledger.tally_model(body, 7)
    assert ledger.MODEL_CALLS == {"unknown": 7}
    ledger.MODEL_CALLS.clear()
    ledger.tally_model("tlwu-usage: model=sonnet tool_calls=3 minutes=1", 3)
    assert ledger.MODEL_CALLS == {"sonnet": 3}
    ledger.MODEL_CALLS.clear()
