"""#1473: decision function of the rule-only merge gate (rules sections 2, 3, 14, 17)."""
import sys
from datetime import datetime, timedelta, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from merge_gate import evaluate, is_protected, usage_ok, linked_issue  # noqa: E402

HEAD_AT = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
BODY = "fix things\n\nCloses #100\n\nOwner comments: none\n\ntlwu-usage: model=sonnet minutes=12"
SHA = "4942427abcdef0123456789"
TEST_OK = [{"name": "test", "status": "completed", "conclusion": "success"}]


def at(minutes):
    return (HEAD_AT + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def pr(**kw):
    p = {"base": {"ref": "dev"}, "head": {"ref": "fix/100", "repo": {"full_name": "sunososobro-hub/tinglongwu"},
                                          "sha": SHA},
         "draft": False, "mergeable_state": "clean", "changed_files": 1, "body": BODY, "labels": []}
    p.update(kw)
    return p


FILES = [{"filename": "scripts/foo.py", "status": "modified"}]
ISSUE = {"number": 100, "labels": [{"name": "status:review"}], "author_association": "OWNER",
         "user": {"login": "sunososobro-hub"}}


def run(p=None, files=FILES, checks=TEST_OK, issue=ISSUE, notes=(), threads=0, bump=None, freeze=False, carry=None):
    p = p or pr()
    if files is not FILES:
        p = {**p, "changed_files": len(files)}
    return evaluate(p, files, checks, issue, list(notes), threads, HEAD_AT, bump, freeze, carry)


def test_clean_pr_merges():
    assert run()[0] == "merge"


def test_other_checks_red_do_not_block_on_dev():  # section 17: only `test` gates
    checks = TEST_OK + [{"name": "e2e", "status": "completed", "conclusion": "failure"}]
    assert run(p=pr(mergeable_state="unstable"), checks=checks)[0] == "merge"


def test_test_pending_or_missing_waits_and_red_holds():
    assert run(checks=[])[0] == "wait"
    assert run(checks=[{"name": "test", "status": "in_progress", "conclusion": None}])[0] == "wait"
    assert run(checks=[{"name": "test", "status": "completed", "conclusion": "failure"}])[0] == "hold"


def test_skips_non_dev_draft_fork_release():
    assert run(p=pr(base={"ref": "main"}))[0] == "skip"
    assert run(p=pr(draft=True))[0] == "skip"
    fork = pr(head={"ref": "fix/100", "repo": {"full_name": "someone/tinglongwu"}, "sha": "abc"})
    assert run(p=fork)[0] == "skip"
    rel = pr(head={"ref": "release/v0.4.9", "repo": {"full_name": "sunososobro-hub/tinglongwu"}, "sha": "abc"})
    assert run(p=rel)[0] == "skip"


def test_bad_head_name_needs_opus():  # section 2
    p = pr(head={"ref": "docs/x", "repo": {"full_name": "sunososobro-hub/tinglongwu"}, "sha": "abc"})
    assert run(p=p)[0] == "needs-opus"


def test_retry_and_feat_heads_ok():
    for ref in ("fix/100-r3", "feat/100"):
        p = pr(head={"ref": ref, "repo": {"full_name": "sunososobro-hub/tinglongwu"}, "sha": "abc"})
        assert run(p=p)[0] == "merge", ref


def test_paperwork_goes_to_ai():
    assert run(p=pr(body="no link\ntlwu-usage: m minutes=3"))[0] == "needs-ai"
    assert run(p=pr(body="Closes #100"))[0] == "needs-ai"


def test_usage_line_survives_coderabbit_block():
    body = BODY + "\n<!-- This is an auto-generated comment: release notes by coderabbit.ai -->\nx\n" \
                  "<!-- end of auto-generated comment: release notes by coderabbit.ai -->"
    assert usage_ok(body) and linked_issue(body) == 100


def test_metadata_only_inside_bot_block_does_not_count():
    body = "x\n<!-- This is an auto-generated comment: summary by coderabbit.ai -->\nCloses #100\n" \
           "tlwu-usage: m minutes=3\n<!-- end of auto-generated comment: summary by coderabbit.ai -->"
    assert linked_issue(body) is None and not usage_ok(body)


def test_closed_pr_skipped():
    assert run(p=pr(state="closed"))[0] == "skip"
    p = pr(state="closed", head={"ref": "hotfix/v0.4.9", "repo": {"full_name": "sunososobro-hub/tinglongwu"}, "sha": SHA})
    assert run(p=p)[0] == "skip"


def test_issue_hard_stops():
    for lab in ("status:frozen", "status:proposal", "needs-owner"):
        assert run(issue={"labels": [{"name": lab}]})[0] == "hold", lab
    assert run(issue=None)[0] == "hold"


def test_protected_paths():
    for path in (".agent/tlwu_rules.md", ".claude/settings.json", ".github/workflows/ci.yml",
                 ".github/workflows/auto-release.yml", "pyproject.toml", "requirements/requirements.txt",
                 "scripts/ci_offline.sh", "src/system/tinglongwu/offline.py", "release/packaging/x.spec", "src/core/tinglongwu/__init__.py"):
        assert is_protected(path), path
    assert is_protected("scripts/merge_gate.py") and is_protected(".github/workflows/merge-gate.yml")
    for path in ("tests/test_netguard.py", ".github/workflows/triage.yml", "src/core/tinglongwu/foo.py",
                 ".github/ISSUE_TEMPLATE/bug.yml"):
        assert not is_protected(path), path


def test_protected_needs_opus_until_newer_pass():
    files = [{"filename": "scripts/release_notes.py", "status": "modified"}]
    assert run(files=files)[0] == "needs-opus"
    old = [{"author": "sunososobro-hub", "body": "Opus review: PASS", "at": at(-5)}]
    assert run(files=files, notes=old)[0] == "needs-opus"
    other = [{"author": "rando", "body": "Opus review: PASS", "at": at(5)}]
    assert run(files=files, notes=other)[0] == "needs-opus"
    unbound = [{"author": "sunososobro-hub", "body": "Opus review: PASS — ok", "at": at(5)}]
    assert run(files=files, notes=unbound)[0] == "needs-opus"  # time alone never binds a PASS to the head
    new = [{"author": "sunososobro-hub", "body": f"Opus review: PASS at {SHA[:7]}", "at": at(5)}]
    assert run(files=files, notes=new)[0] == "merge"
    review = [{"author": "sunososobro-hub", "body": "Opus review: PASS", "at": at(5), "commit_id": SHA}]
    assert run(files=files, notes=review)[0] == "merge"
    other_line = [{"author": "sunososobro-hub", "body": f"Opus review: PASS 0123abc\nhead is {SHA[:7]}", "at": at(5)}]
    assert run(files=files, notes=other_line)[0] == "needs-opus"  # the SHA must sit on the PASS line
    later_ref = [{"author": "sunososobro-hub", "body": f"Opus review: PASS at 0123abc (head: {SHA[:7]})", "at": at(5)}]
    assert run(files=files, notes=later_ref)[0] == "needs-opus"  # only the first SHA on the line is the approval
    lookalike = [{"author": "sunososobro-hub", "body": f"Opus review: PASS {SHA[:7]}ffff", "at": at(5)}]
    assert run(files=files, notes=lookalike)[0] == "needs-opus"  # names another SHA with the same prefix
    stale = [{"author": "sunososobro-hub", "body": "Opus review: PASS", "at": at(5), "commit_id": "0" * 40}]
    assert run(files=files, notes=stale)[0] == "needs-opus"


def test_exempt_bump_merges_without_opus():
    files = [{"filename": "src/core/tinglongwu/__init__.py", "status": "modified"}]
    assert run(files=files, bump=lambda fs: (True, "version line only"))[0] == "merge"
    assert run(files=files, bump=lambda fs: (False, "no"))[0] == "needs-opus"


def test_needs_owner_comment_holds_until_later_pass():
    stop = {"author": "sunososobro-hub", "body": "needs owner: which way?", "at": at(1)}
    assert run(notes=[stop])[0] == "hold"
    ok = {"author": "sunososobro-hub", "body": f"Opus review: PASS {SHA[:7]}", "at": at(2)}
    assert run(notes=[stop, ok])[0] == "merge"


def test_fail_newer_than_head_holds_older_does_not():
    fail = {"author": "sunososobro-hub", "body": "AI review: FAIL — greps source", "at": at(3)}
    assert run(notes=[fail])[0] == "hold"
    assert run(notes=[{**fail, "at": at(-3)}])[0] == "merge"


def test_unresolved_threads_and_dirty_go_to_ai():
    assert run(threads=2)[0] == "needs-ai"
    assert run(p=pr(mergeable_state="dirty"))[0] == "needs-ai"
    assert run(p=pr(mergeable_state="unknown"))[0] == "wait"


def test_truncated_file_list_waits():
    assert evaluate(pr(changed_files=5), FILES, TEST_OK, ISSUE, [], 0, HEAD_AT)[0] == "wait"


def test_legacy_freeze_needs_release_fix():
    assert run(freeze=True)[0] == "hold"
    assert run(p=pr(body=BODY.replace("fix things", "fix things release-fix")), freeze=True)[0] == "merge"


def test_hotfix_backmerge_goes_to_ai():
    p = pr(head={"ref": "hotfix/v0.4.9", "repo": {"full_name": "sunososobro-hub/tinglongwu"}, "sha": "abc"})
    assert run(p=p)[0] == "needs-ai"


def test_pr_labels_hold():  # Opus CHANGES on aa3fcb3: an AI merger's needs-opus / needs-owner on the PR is a stop
    assert run(p=pr(labels=[{"name": "needs-owner"}]))[0] == "hold"
    passed = [{"author": "sunososobro-hub", "body": f"Opus review: PASS {SHA[:7]}", "at": at(5)}]
    assert run(p=pr(labels=[{"name": "needs-owner"}]), notes=passed)[0] == "hold"
    assert run(p=pr(labels=[{"name": "needs-opus"}]))[0] == "hold"
    assert run(p=pr(labels=[{"name": "needs-opus"}]), notes=passed)[0] == "merge"
    assert run(p=pr(labels=[{"name": "needs-review-ai"}]))[0] == "merge"


def test_protected_path_needs_collaborator_issue_even_with_pass():  # section 14
    files = [{"filename": "scripts/release_notes.py", "status": "modified"}]
    passed = [{"author": "sunososobro-hub", "body": f"Opus review: PASS {SHA[:7]}", "at": at(5)}]
    outside = {**ISSUE, "author_association": "NONE", "user": {"login": "rando"}}
    assert run(files=files, issue=outside, notes=passed)[0] == "hold"
    assert run(files=files, issue={**outside, "author_association": "CONTRIBUTOR"}, notes=passed)[0] == "hold"
    for assoc in ("OWNER", "MEMBER", "COLLABORATOR"):
        assert run(files=files, issue={**outside, "author_association": assoc}, notes=passed)[0] == "merge", assoc
    bump_issue = {**outside, "user": {"login": "github-actions[bot]"}}
    init = [{"filename": "src/core/tinglongwu/__init__.py", "status": "modified"}]
    assert run(files=init, issue=bump_issue, bump=lambda fs: (True, "version line only"))[0] == "merge"
    assert run(files=init, issue=outside, bump=lambda fs: (True, "version line only"))[0] == "hold"
    assert run(issue=outside)[0] == "merge"  # non-protected code from an outside issue follows the normal gates


def test_pass_on_older_head_carries_when_no_protected_change():  # #1580
    files = [{"filename": ".github/workflows/e2e.yml", "status": "modified"},
             {"filename": "scripts/ci_e2e.py", "status": "modified"}]
    older = "96493d2a26210c7fbd9be77ea4d75dd4f11fe794"
    note = [{"author": "sunososobro-hub", "body": f"Opus review: PASS {older} - ok", "at": at(-30)}]
    assert run(files=files, notes=note)[0] == "needs-opus"  # no carry callable: old behaviour
    assert run(files=files, notes=note, carry=lambda s: older.startswith(s))[0] == "merge"
    assert run(files=files, notes=note, carry=lambda s: False)[0] == "needs-opus"  # protected changed since
    review = [{"author": "sunososobro-hub", "body": "Opus review: PASS", "at": at(-30), "commit_id": older}]
    assert run(files=files, notes=review, carry=lambda s: s == older)[0] == "merge"
    rando = [{"author": "rando", "body": f"Opus review: PASS {older}", "at": at(-30)}]
    assert run(files=files, notes=rando, carry=lambda s: True)[0] == "needs-opus"
    no_sha = [{"author": "sunososobro-hub", "body": "Opus review: PASS looks fine", "at": at(-30)}]
    assert run(files=files, notes=no_sha, carry=lambda s: True)[0] == "needs-opus"
    short = [{"author": "sunososobro-hub", "body": "Opus review: PASS 96493d2 - ok", "at": at(-30)}]
    assert run(files=files, notes=short, carry=lambda s: True)[0] == "needs-opus"  # short SHA never carries


def test_carried_pass_loses_to_later_changes():  # #1580 review: PASS on A, CHANGES on A, test-only push
    files = [{"filename": ".github/workflows/e2e.yml", "status": "modified"}]
    older = "96493d2a26210c7fbd9be77ea4d75dd4f11fe794"
    notes = [{"author": "sunososobro-hub", "body": f"Opus review: PASS {older}", "at": at(-30)},
             {"author": "sunososobro-hub", "body": "Opus review: CHANGES - protected code wrong", "at": at(-20)}]
    assert run(files=files, notes=notes, carry=lambda s: True)[0] == "needs-opus"
    stop = [notes[0], {"author": "sunososobro-hub", "body": "this is blocking", "at": at(-20)}]
    assert run(files=files, notes=stop, carry=lambda s: True)[0] != "merge"
    again = notes + [{"author": "sunososobro-hub", "body": f"Opus review: PASS {older}", "at": at(-10)}]
    assert run(files=files, notes=again, carry=lambda s: True)[0] == "merge"  # a PASS after the CHANGES carries


def test_pass_carries_checks_compare(monkeypatch):  # #1580: I/O helper, fail closed
    import merge_gate as mg
    head = "b" * 40
    def fake(cmp):
        monkeypatch.setattr(mg, "_gh", lambda path, *a: cmp)
    full = "96493d2a26210c7fbd9be77ea4d75dd4f11fe794"
    base = {"base_commit": {"sha": full}}
    fake({**base, "status": "ahead", "behind_by": 0, "files": [{"filename": "scripts/ci_e2e.py"}]})
    assert mg.pass_carries(full, head)
    assert not mg.pass_carries("96493d2", head)  # short SHA: could resolve to a branch or tag
    fake({"base_commit": {"sha": "c" * 40}, "status": "ahead", "behind_by": 0, "files": []})
    assert not mg.pass_carries(full, head)  # compare base is not the approved commit
    fake({**base, "status": "ahead", "behind_by": 0, "files": [{"filename": ".github/workflows/e2e.yml"}]})
    assert not mg.pass_carries(full, head)
    fake({**base, "status": "ahead", "behind_by": 0,
          "files": [{"filename": "x.py", "previous_filename": ".claude/hook.sh"}]})
    assert not mg.pass_carries(full, head)
    fake({**base, "status": "diverged", "behind_by": 2, "files": []})
    assert not mg.pass_carries(full, head)
    fake({**base, "status": "ahead", "behind_by": 0, "files": [{"filename": f"f{i}.py"} for i in range(300)]})
    assert not mg.pass_carries(full, head)
    assert not mg.pass_carries("not-a-sha", head)


MOBILE_FILES = [{"filename": "mobile/lib/main.dart", "status": "modified"}]


def test_mobile_pr_needs_mobile_apk_green():  # #1618
    ok = {"name": "mobile-apk", "status": "completed", "conclusion": "success"}
    assert run(files=MOBILE_FILES, checks=TEST_OK + [ok])[0] == "merge"
    assert run(files=MOBILE_FILES, checks=TEST_OK + [{**ok, "conclusion": "failure"}]) == ("hold", "mobile-apk is red")
    assert run(files=MOBILE_FILES, checks=TEST_OK + [{**ok, "status": "in_progress", "conclusion": None}])[0] == "wait"
    assert run(files=MOBILE_FILES, checks=TEST_OK)[0] == "wait"  # no mobile-apk run yet: check once, skip


def test_non_mobile_pr_ignores_mobile_apk():  # #1618
    red = {"name": "mobile-apk", "status": "completed", "conclusion": "failure"}
    assert run(checks=TEST_OK + [red])[0] == "merge"


EVAL_OK = {"name": "mini-eval", "status": "completed", "conclusion": "success"}


def test_asr_mt_eval_pr_needs_mini_eval_green():  # #1687
    for path in ("src/ui/tinglongwu/asr.py", "src/core/tinglongwu/mt.py", "src/flow/tinglongwu/x.py", "eval/baseline.json",
                 "eval/tlwu_eval/run.py", "src/system/tinglongwu_setup/manifest.json", ".github/workflows/eval.yml"):
        files = [{"filename": path, "status": "modified"}]
        assert run(files=files, checks=TEST_OK + [EVAL_OK])[0] == "merge", path
        assert run(files=files, checks=TEST_OK + [{**EVAL_OK, "conclusion": "failure"}]) == ("hold", "mini-eval is red")
        assert run(files=files, checks=TEST_OK + [{**EVAL_OK, "status": "queued", "conclusion": None}])[0] == "wait"
        assert run(files=files, checks=TEST_OK)[0] == "wait", path  # no mini-eval yet: check once, skip
    moved = [{"filename": "docs/x.md", "previous_filename": "src/core/tinglongwu/x.md", "status": "renamed"}]
    assert run(files=moved, checks=TEST_OK)[0] == "wait"


def test_non_eval_pr_ignores_eval_and_other_checks():  # #1687: e2e, low-end-cpu, eval-terms, CodeRabbit never block
    others = [{"name": n, "status": s, "conclusion": c} for n, s, c in (
        ("mini-eval", "completed", "failure"), ("low-end-cpu", "in_progress", None), ("eval-terms", "completed",
         "failure"), ("e2e (ubuntu-latest)", "queued", None), ("CodeRabbit", "in_progress", None))]
    assert run(checks=TEST_OK + others)[0] == "merge"
    files = [{"filename": "src/core/tinglongwu/asr.py", "status": "modified"}]
    assert run(files=files, checks=TEST_OK + [EVAL_OK] + others[1:])[0] == "merge"


def test_eval_path_re_matches_eval_yml_filter():  # #1687: the gate and the workflow must agree on which PRs run it
    import pathlib
    import merge_gate as mg
    wf = (pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/eval.yml").read_text(encoding="utf-8")
    assert "src/[^/]+/tinglongwu/" in wf.split("mini-eval:", 1)[1].split("low-end-cpu", 1)[0]
    assert not mg.EVAL_PATH_RE.match("mobile/lib/main.dart") and not mg.EVAL_PATH_RE.match("docs/x.md")


# Owner 2026-11-20 merge lanes: internal = owner-authored PR + linked issue labelled src:owner
OWNER_PR = {"user": {"login": "sunososobro-hub"}}
OWNER_ISSUE = {**ISSUE, "labels": [{"name": "status:review"}, {"name": "src:owner"}]}
DOCS = [{"filename": "docs/guide.md", "status": "modified"}, {"filename": "README.md", "status": "modified"}]


def test_lane1_internal_docs_only_needs_no_test():
    assert run(pr(**OWNER_PR), files=DOCS, checks=[], issue=OWNER_ISSUE)[0] == "merge"
    assert run(pr(**OWNER_PR), files=DOCS, checks=[], issue=ISSUE)[0] == "wait"  # no src:owner: test still needed
    assert run(pr(), files=DOCS, checks=[], issue=OWNER_ISSUE)[0] == "wait"  # not owner-authored
    mixed = DOCS + [{"filename": "scripts/foo.py", "status": "modified"}]
    assert run(pr(**OWNER_PR), files=mixed, checks=[], issue=OWNER_ISSUE)[0] == "wait"  # code: lane 2, test needed
    rules = [{"filename": ".agent/tlwu_rules.md", "status": "modified"}]
    assert run(pr(**OWNER_PR), files=rules, checks=[], issue=OWNER_ISSUE)[0] == "wait"  # .agent/ is never lane 1


def test_lane2_internal_code_needs_no_opus_even_protected():
    for path in ("scripts/release_notes.py", "src/core/tinglongwu/__init__.py", ".agent/tlwu_rules.md"):
        files = [{"filename": path, "status": "modified"}]
        ok = TEST_OK + [{"name": "mini-eval", "status": "completed", "conclusion": "success"}]
        assert run(pr(**OWNER_PR), files=files, checks=ok, issue=OWNER_ISSUE)[0] == "merge", path
        assert run(pr(**OWNER_PR), files=files, checks=[], issue=OWNER_ISSUE)[0] == "wait", path  # test still required
        red = [{**TEST_OK[0], "conclusion": "failure"}]
        assert run(pr(**OWNER_PR), files=files, checks=red, issue=OWNER_ISSUE)[0] == "hold", path
    assert run(pr(**OWNER_PR, labels=[{"name": "needs-opus"}]), issue=OWNER_ISSUE)[0] == "merge"
    assert run(pr(**OWNER_PR, labels=[{"name": "needs-owner"}]), issue=OWNER_ISSUE)[0] == "hold"
    assert run(pr(**OWNER_PR), files=MOBILE_FILES, issue=OWNER_ISSUE)[0] == "wait"  # mobile-apk still required


def test_lane3_external_and_ai_issues_stay_strict():
    files = [{"filename": "scripts/release_notes.py", "status": "modified"}]
    ai_issue = {**ISSUE, "labels": [{"name": "src:ai"}]}
    assert run(pr(**OWNER_PR), files=files, issue=ai_issue)[0] == "needs-opus"
    assert run(pr(**OWNER_PR), files=DOCS, checks=[], issue=ai_issue)[0] == "wait"
    assert run(pr(user={"login": "rando"}), files=files, issue=OWNER_ISSUE)[0] == "needs-opus"
    assert run(pr(user={"login": "rando"}), files=DOCS, checks=[], issue=OWNER_ISSUE)[0] == "wait"
