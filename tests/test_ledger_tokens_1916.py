"""#1916: tokens per run in the tlwu-usage trailer, the ledger's tokens-by-model table, and the session reader."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ledger  # noqa: E402
import session_tokens  # noqa: E402


def test_pr_tokens_present_and_missing():
    body = "x\ntlwu-usage: model=sonnet tool_calls=9 tokens_in=1200 tokens_out=300 tokens_cache=5000 minutes=7"
    assert ledger.pr_tokens(body) == (1200, 300)
    assert ledger.pr_tokens("tlwu-usage: model=sonnet tool_calls=9 minutes=7") is None
    assert ledger.pr_tokens("tokens_in=1 tokens_out=2\nlast line") is None
    assert ledger.pr_tokens(None) is None


def test_token_rows_average_and_ignore_missing():
    prs = [{"model": "sonnet", "tokens": (100, 10)}, {"model": "sonnet", "tokens": (300, 30)},
           {"model": "sonnet", "tokens": None}, {"model": "opus"}]
    assert ledger.token_rows(prs) == [{"model": "sonnet", "prs": 2, "in_avg": 200, "out_avg": 20}]
    assert "| sonnet | 2 | 200 | 20 |" in ledger.render_tokens(ledger.token_rows(prs))


def test_render_tokens_empty_when_no_fields():
    assert ledger.render_tokens(ledger.token_rows([{"model": "sonnet", "tokens": None}])) == ""
    assert ledger.render_tokens([]) == ""


def _line(mid, i, o, cw, cr):
    return json.dumps({"message": {"id": mid, "usage": {"input_tokens": i, "output_tokens": o,
                                                          "cache_creation_input_tokens": cw,
                                                          "cache_read_input_tokens": cr}}})


def test_sum_usage_counts_repeated_message_once():
    lines = [_line("a", 2, 10, 100, 1000), _line("a", 2, 10, 100, 1000), _line("b", 3, 5, 0, 2000), "not json",
             json.dumps({"type": "user"})]
    tot = session_tokens.sum_usage(lines)
    assert tot == {"in": 2 + 100 + 3, "out": 15, "cache": 3000}
    assert session_tokens.trailer_fields(tot) == "tokens_in=105 tokens_out=15 tokens_cache=3000"


def test_sum_usage_none_without_usage():
    assert session_tokens.sum_usage(["{}", "garbage"]) is None


def test_cli_prints_fields_and_fails_quietly(tmp_path):
    good = tmp_path / "s.jsonl"
    good.write_text(_line("a", 1, 2, 3, 4) + "\n")
    r = subprocess.run([sys.executable, str(ROOT / "scripts/session_tokens.py"), str(good)], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "tokens_in=4 tokens_out=2 tokens_cache=4"
    bad = tmp_path / "e.jsonl"
    bad.write_text("{}\n")
    r = subprocess.run([sys.executable, str(ROOT / "scripts/session_tokens.py"), str(bad)], capture_output=True, text=True)
    assert r.returncode == 1 and r.stdout == ""
