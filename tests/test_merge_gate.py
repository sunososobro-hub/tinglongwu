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


FILES = [{"filename": "tinglongwu/foo.py", "status": "modified"}]
ISSUE = {"number": 100, "labels": [{"name": "status:review"}], "author_association": "OWNER",
         "user": {"login": "sunososobro-hub"}}


def run(p=None, files=FILES, checks=TEST_OK, issue=ISSUE, notes=(), threads=0, bump=None, freeze=False):
    p = p or pr()
    if files is not FILES:
        p = {**p, "changed_files": len(files)}
    return evaluate(p, files, checks, issue, list(notes), threads, HEAD_AT, bump, freeze)


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
                 ".github/workflows/auto-release.yml", "pyproject.toml", "requirements-dev.txt",
                 "scripts/ci_offline.sh", "tinglongwu/offline.py", "packaging/x.spec", "tinglongwu/__init__.py"):
        assert is_protected(path), path
    assert is_protected("scripts/merge_gate.py") and is_protected(".github/workflows/merge-gate.yml")
    for path in ("tests/test_netguard.py", ".github/workflows/triage.yml", "tinglongwu/foo.py",
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
    files = [{"filename": "tinglongwu/__init__.py", "status": "modified"}]
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
    init = [{"filename": "tinglongwu/__init__.py", "status": "modified"}]
    assert run(files=init, issue=bump_issue, bump=lambda fs: (True, "version line only"))[0] == "merge"
    assert run(files=init, issue=outside, bump=lambda fs: (True, "version line only"))[0] == "hold"
    assert run(issue=outside)[0] == "merge"  # non-protected code from an outside issue follows the normal gates
