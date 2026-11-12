import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_credits as cc  # noqa: E402

PRS = [
    {"user": {"login": "sunososobro-hub"}, "merged_at": "2026-11-10T00:00:00Z"},
    {"user": {"login": "jchodev"}, "merged_at": "2026-11-10T00:00:00Z"},
    {"user": {"login": "oldtimer"}, "merged_at": "2026-01-01T00:00:00Z"},
    {"user": {"login": "dependabot[bot]"}, "merged_at": "2026-11-10T00:00:00Z"},
    {"user": {"login": "closedonly"}, "merged_at": None},
]


def test_external_authors_filters_owner_bots_old_unmerged():
    assert cc.external_authors(PRS, "2026-06-01T00:00:00Z", "sunososobro-hub") == ["jchodev"]


def test_missing_credits_whole_word():
    assert cc.missing_credits(["jchodev"], "thanks jchodev2") == ["jchodev"]
    assert cc.missing_credits(["jchodev"], "thanks @JChodev!") == []


def _run(tmp_path, credits_text):
    prs = tmp_path / "prs.json"
    prs.write_text(json.dumps(PRS))
    cr = tmp_path / "CREDITS.md"
    cr.write_text(credits_text)
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "check_credits.py"), "--prs-json", str(prs),
                           "--since", "2026-06-01T00:00:00Z", "--credits", str(cr)],
                          capture_output=True, text=True)


def test_cli_exit_1_when_missing(tmp_path):
    r = _run(tmp_path, "nobody here")
    assert r.returncode == 1 and "jchodev" in r.stdout


def test_cli_exit_0_when_credited(tmp_path):
    r = _run(tmp_path, "- jchodev: Ollama engine")
    assert r.returncode == 0, r.stdout


def test_commit_author_and_coauthor_counted():
    prs = [{"user": {"login": "sunososobro-hub"}, "merged_at": "2026-11-10T00:00:00Z",
            "commits": [{"author": {"login": "cook1e0728"}, "commit": {"message": "x"}},
                        {"author": {"login": "sunososobro-hub"},
                         "commit": {"message": "y\n\nCo-authored-by: helper1 <h@example.com>"}}]}]
    assert cc.external_authors(prs, "", "sunososobro-hub") == ["cook1e0728", "helper1"]


def test_cli_owner_pr_with_external_commit_author(tmp_path):
    prs = tmp_path / "prs.json"
    prs.write_text(json.dumps([{"user": {"login": "sunososobro-hub"}, "merged_at": "2026-11-10T00:00:00Z",
                                "commits": [{"author": {"login": "cook1e0728"}, "commit": {"message": "x"}}]}]))
    cr = tmp_path / "CREDITS.md"
    cmd = [sys.executable, str(ROOT / "scripts" / "check_credits.py"), "--prs-json", str(prs), "--credits", str(cr)]
    cr.write_text("nobody")
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 1 and "cook1e0728" in r.stdout
    cr.write_text("- cook1e0728")
    assert subprocess.run(cmd, capture_output=True, text=True).returncode == 0
