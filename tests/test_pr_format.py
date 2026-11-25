"""#1640: rule-only PR format lint (scripts/pr_format.py): decisions, comments, stale sweep and the I/O it does."""
import sys
from datetime import datetime, timedelta, UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pr_format  # noqa: E402
from pr_format import decide, missing, stale, comment_text, STALE_DAYS  # noqa: E402

REPO = "sunososobro-hub/tinglongwu"
GOOD = "fix things\n\nOwner comments: none\n\nCloses #100\n\ntlwu-usage: model=sonnet minutes=12"


def pr(head="fix/100", body=GOOD, assoc="OWNER", repo=REPO, **kw):
    p = {"number": 7, "state": "open", "draft": False, "base": {"ref": "dev"},
         "head": {"ref": head, "repo": {"full_name": repo}}, "body": body, "author_association": assoc,
         "user": {"login": "someone", "type": "User"}, "labels": [], "updated_at": "2026-11-20T00:00:00Z"}
    p.update(kw)
    return p


def test_compliant_pr_is_untouched():
    for head in ("fix/100", "fix/100-r2", "feat/100"):
        assert decide(pr(head=head)) == ("ok", [])
    assert decide(pr(body=GOOD.replace("Closes", "Refs"))) == ("ok", [])


def test_collaborator_pr_with_bad_head_is_closed():
    assert decide(pr(head="rules/tweak")) == ("close", ["head"])
    assert decide(pr(head="fix/100-docs", assoc="COLLABORATOR")) == ("close", ["head"])


def test_missing_link_and_usage_are_reported():
    body = "fix things\n\ntlwu-usage: model=sonnet minutes=12"
    assert decide(pr(body=body)) == ("close", ["link"])
    assert decide(pr(body="Closes #100")) == ("close", ["usage"])
    assert decide(pr(head="x", body="")) == ("close", ["head", "link", "usage"])
    assert missing(pr(body="Closes #100\ntlwu-usage: model=opus")) == ["usage"]  # no minutes=N


def test_link_inside_html_comment_does_not_count():
    body = "<!-- Link the issue: Closes #123 -->\n\ntlwu-usage: model=human minutes=0"
    assert decide(pr(body=body)) == ("close", ["link"])


def test_fork_pr_is_labelled_never_closed():
    assert decide(pr(head="patch-1", body="my fix", assoc="CONTRIBUTOR", repo="stranger/tinglongwu")) == \
        ("label", ["head", "link", "usage"])
    # a collaborator account pushing from its fork is still treated as outside: never auto-closed
    assert decide(pr(head="patch-1", repo="owner-fork/tinglongwu"))[0] == "label"
    assert decide(pr(assoc="FIRST_TIME_CONTRIBUTOR", repo="stranger/tinglongwu")) == ("ok", [])


def test_exempt_prs_are_skipped():
    assert decide(pr(head="hotfix/v1.2.3", body=""))[0] == "skip"
    assert decide(pr(head="release/v1.2.3", body=""))[0] == "skip"
    assert decide(pr(head="main", body=""))[0] == "skip"  # section 11 dev <- main sync
    assert decide(pr(head="x", base={"ref": "main"}))[0] == "skip"
    assert decide(pr(head="x", draft=True))[0] == "skip"
    assert decide(pr(head="x", state="closed"))[0] == "skip"
    assert decide(pr(head="x", user={"login": "dependabot[bot]", "type": "Bot"}))[0] == "skip"


def test_comments_name_each_fix():
    close = comment_text("close", ["head", "usage"])
    assert close.startswith(pr_format.MARKER) and "fix/<issue number>" in close and "tlwu-usage" in close
    friendly = comment_text("label", ["link"])
    assert "Thanks" in friendly and "Closes #<issue number>" in friendly and f"{STALE_DAYS} days" in friendly


def test_stale_only_after_seven_idle_days():
    now = datetime(2026, 11, 30, tzinfo=UTC)
    lab = [{"name": "needs-format"}]
    old = (now - timedelta(days=STALE_DAYS, minutes=1)).isoformat()
    fresh = (now - timedelta(days=STALE_DAYS - 1)).isoformat()
    assert stale(pr(labels=lab, updated_at=old), now)
    assert not stale(pr(labels=lab, updated_at=fresh), now)
    assert not stale(pr(updated_at=old), now)  # no needs-format label


def test_pr_template_filled_in_passes_and_unfilled_fails():
    tpl = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    assert decide(pr(body=tpl)) == ("close", ["link"])  # `Closes #` left empty
    filled = tpl.replace("Closes #\n", "Closes #100\n")
    assert decide(pr(body=filled)) == ("ok", [])
    assert tpl.rstrip().splitlines()[-1].startswith("tlwu-usage:")


def _record(monkeypatch, comments=()):
    calls = []

    def fake(*args):
        calls.append(args)
        return list(comments) if args[0].endswith("/comments?per_page=100") else None
    monkeypatch.setattr(pr_format, "_gh", fake)
    return calls


def test_apply_close_comments_closes_and_clears_merge_gate_labels(monkeypatch):
    calls = _record(monkeypatch)
    p = pr(head="x", labels=[{"name": "needs-opus"}])
    pr_format.apply(7, p, "close", ["head"])
    flat = [" ".join(c) for c in calls]
    assert any("POST" in c and "issues/7/comments" in c for c in flat)
    assert any("PATCH" in c and "pulls/7" in c and "state=closed" in c for c in flat)
    assert any("DELETE" in c and "labels/needs-opus" in c for c in flat)


def test_apply_label_comments_once_and_ok_removes_label(monkeypatch):
    calls = _record(monkeypatch, comments=[{"body": pr_format.MARKER + "\nold"}])
    pr_format.apply(7, pr(head="x", repo="a/b"), "label", ["head"])
    flat = [" ".join(c) for c in calls]
    assert not any("POST" in c and "issues/7/comments" in c for c in flat)  # already commented
    assert any("labels[]=needs-format" in c for c in flat)
    assert not any("PATCH" in c for c in flat)  # never closed
    calls.clear()
    pr_format.apply(7, pr(labels=[{"name": "needs-format"}]), "ok", [])
    assert [c[:2] for c in calls] == [("-X", "DELETE")]


def test_main_dry_run_prints_decision(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(pr_format, "_gh", lambda *a: calls.append(a) or pr(head="x"))
    assert pr_format.main(["--pr", "7"]) == 0
    assert "#7 close: head" in capsys.readouterr().out
    assert all("-X" not in c for c in calls)  # dry run writes nothing
