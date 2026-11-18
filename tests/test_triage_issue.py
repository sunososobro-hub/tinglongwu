"""Behaviour tests for scripts/triage_issue.py (rule-based issue triage, rules sections 10/12/15)."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "triage_issue", Path(__file__).resolve().parents[1] / "scripts" / "triage_issue.py")
triage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(triage)

FULL_BODY = "Why: owner asked.\nGoal: x.\nAcceptance: pytest passes.\nVerify: pytest -q\n"


def test_complete_owner_issue_gets_defaults_and_ready_labels():
    r = triage.decide("[bug] crash on start", FULL_BODY, ["P2", "area:asr"], "OWNER")
    assert r["reasons"] == []
    assert set(r["add"]) == {"bug", "model:sonnet", "mode:listen", "src:owner", "status:todo", "ok-to-work"}


def test_existing_labels_are_not_overridden():
    r = triage.decide("[bug] x", FULL_BODY, ["enhancement", "P1", "model:opus", "area:gui", "mode:two-way",
                                              "src:ai", "status:proposal"], "OWNER")
    assert r == {"add": [], "reasons": []}


def test_missing_priority_and_area_goes_to_needs_triage_without_ok_to_work():
    r = triage.decide("[feat] new thing", FULL_BODY, [], "OWNER")
    assert "needs-triage" in r["add"]
    assert "ok-to-work" not in r["add"] and "status:todo" not in r["add"]
    assert any("P0..P3" in x for x in r["reasons"])
    assert any("area" in x for x in r["reasons"])


def test_dev_red_prefix_follows_section_17():
    r = triage.decide("[dev] e2e failing on dev", FULL_BODY, [], "OWNER")
    assert r["reasons"] == []
    assert {"bug", "P1", "area:ci", "mode:shared"} <= set(r["add"])


def test_two_areas_and_unknown_mode_need_triage():
    r = triage.decide("[bug] x", FULL_BODY, ["P2", "area:gui", "area:audio"], "OWNER")
    assert "needs-triage" in r["add"]
    r = triage.decide("[bug] x", FULL_BODY, ["P2", "area:gui"], "OWNER")
    assert any("mode" in x for x in r["reasons"])


def test_format_and_language_checks():
    r = triage.decide("[bug] x", "Why: a\nGoal: b\n", ["P2", "area:ci"], "OWNER")
    assert any("Acceptance:" in x and "Verify:" in x for x in r["reasons"])
    r = triage.decide("[bug] 字幕不見", FULL_BODY, ["P2", "area:ci"], "OWNER")
    assert any("English" in x for x in r["reasons"])
    assert triage._field_present("**Acceptance:** done", "Acceptance")
    assert triage._field_present("### Verify\nrun it", "Verify")


def test_outside_author_never_gets_ok_to_work():
    r = triage.decide("[bug] x", FULL_BODY, ["P2", "area:asr"], "NONE")
    assert "ok-to-work" not in r["add"] and "needs-triage" in r["add"]
    assert "src:user" in r["add"]


def test_ai_enhancement_becomes_proposal():
    r = triage.decide("[idea] y", FULL_BODY, ["P3", "area:tts", "src:ai"], "OWNER")
    assert "status:proposal" in r["add"] and "ok-to-work" not in r["add"]


def test_comment_is_one_line():
    assert "\n" not in triage.comment_text(["a", "b"])
