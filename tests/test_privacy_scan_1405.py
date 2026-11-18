"""#1405 privacy gate. Only made-up words are used here ("zorblat", "qux frim"); never put a real list word in a test."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import privacy_scan as ps  # noqa: E402
import scan_denylist as sd  # noqa: E402

FAKE = "# fake list\nzorblat\nqux frim\n"


class FakeGH:
    repo = "o/r"

    def __init__(self, files=None, issues=(), comments=(), review_comments=(), checks=None, raw=b""):
        self.files, self.issues, self.comments, self.review_comments = files or [], issues, comments, review_comments
        self.checks, self.raw = checks or [], raw
        self.calls: list[tuple] = []

    def get(self, path):
        self.calls.append(("get", path))
        if "/check-runs" in path:
            return {"check_runs": self.checks}
        if path.endswith("/status"):
            return {"statuses": getattr(self, "statuses", [])}
        return {"head": {"sha": "abc1234567"}}

    def post_status(self, sha, state, context, description):
        self.calls.append(("status", sha, state, context))

    def get_raw(self, path):
        self.calls.append(("raw", path))
        return self.raw

    def paginate(self, path):
        path = path.split("?")[0]
        if path.endswith("/files"):
            return iter(self.files)
        if path.endswith("/issues/comments"):
            return iter(self.comments)
        if path.endswith("/pulls/comments"):
            return iter(self.review_comments)
        return iter(self.issues)

    def patch(self, path, body):
        self.calls.append(("patch", path, body))

    def add_label(self, n):
        self.calls.append(("label", n))

    def minimize(self, node_id):
        self.calls.append(("minimize", node_id))


@pytest.fixture
def rx():
    return sd.Matcher(sd.parse_words(FAKE))


# ---- matcher -------------------------------------------------------------
def test_matcher_case_width_and_spaces(rx):
    assert rx.search("a ZorBlat b")
    assert rx.search("full width ＺＯＲＢＬＡＴ here")
    assert rx.search("spaced z o r b l a t")
    assert rx.search("joined quxfrim")  # list entry has a space, text has none
    assert rx.search("ｑｕｘ　ｆｒｉｍ")  # full-width letters and ideographic space
    assert rx.search("zorbl at")  # spaces inside a word are ignored on purpose
    assert not rx.search("zorbla-t")
    assert not rx.search("nothing to see")


def test_mask_keeps_rest_and_hides_word(rx):
    out = rx.sub("pre ＺＯＲＢＬＡＴ post")
    assert out == "pre ******* post"
    assert rx.sub("x qux frim y", "***") == "x *** y"
    assert "zorblat" not in rx.sub("zorblat zorblat").lower()


def test_env_text_and_empty(monkeypatch):
    monkeypatch.delenv("TLWU_DENYLIST_TEXT", raising=False)
    assert sd.env_words() is None
    monkeypatch.setenv("TLWU_DENYLIST_TEXT", "  \n")
    assert sd.env_words() is None
    monkeypatch.setenv("TLWU_DENYLIST_TEXT", FAKE)
    assert sd.env_words() == ["zorblat", "qux frim"]
    assert sd.Matcher(["   ", "#x"]).size == 1  # "#x" is a word when passed directly; blanks are dropped


# ---- PR diff ---------------------------------------------------------------
PATCH = "@@ -1,2 +1,3 @@\n keep\n-old zorblat line\n+new ＺＯＲＢＬＡＴ line\n+fine\n"


def test_pr_hit_in_added_line_is_masked(rx, capsys):
    gh = FakeGH(files=[{"filename": "docs/a.md", "status": "modified", "patch": PATCH}])
    assert ps.cmd_pr(gh, 7, rx=rx) == 1
    out = capsys.readouterr().out
    assert "docs/a.md:2: new ******* line" in out
    assert "ＺＯＲＢＬＡＴ" not in out and "zorblat" not in out.lower()


def test_pr_removed_line_is_not_a_hit(rx):
    patch = "@@ -1,2 +1,1 @@\n-old zorblat line\n keep\n"
    gh = FakeGH(files=[{"filename": "a.py", "status": "modified", "patch": patch},
                       {"filename": "zorblat.txt", "status": "removed"}])
    assert ps.cmd_pr(gh, 7, rx=rx) == 0


def test_pr_file_name_and_no_patch_file(rx, capsys):
    gh = FakeGH(files=[{"filename": "big.txt", "status": "added"}], raw=b"one\ntwo qux frim\n")
    assert ps.cmd_pr(gh, 7, rx=rx) == 1
    assert "big.txt:2: two ***" in capsys.readouterr().out  # "qux frim" masked (8 chars)
    gh2 = FakeGH(files=[{"filename": "x/Zorblat.md", "status": "added", "patch": "@@ -0,0 +1 @@\n+ok\n"}])
    assert ps.cmd_pr(gh2, 7, rx=rx) == 1


def test_pr_no_secret_runs_builtin_patterns_and_passes_when_clean(monkeypatch, capsys):
    monkeypatch.delenv("TLWU_DENYLIST_TEXT", raising=False)
    gh = FakeGH(files=[{"filename": "a.py", "status": "added", "patch": "@@ -0,0 +1 @@\n+ok zorblat\n"}])
    assert ps.cmd_pr(gh, 7) == 0  # no list: private words are not known, built-in patterns are clean
    assert "TLWU_DENYLIST not set" in capsys.readouterr().out
    key = "ghp_" + "a1B2" * 9
    bad = FakeGH(files=[{"filename": "a.py", "status": "added", "patch": f"@@ -0,0 +1 @@\n+token = {key}\n"}])
    assert ps.cmd_pr(bad, 7) == 1
    out = capsys.readouterr().out
    assert "a.py:1: token = " + "*" * len(key) in out and key not in out


def test_pr_no_secret_fork_uses_privacy_scan_check(monkeypatch):
    monkeypatch.delenv("TLWU_DENYLIST_TEXT", raising=False)
    ok = FakeGH(checks=[{"name": "privacy-scan", "status": "completed", "conclusion": "success", "completed_at": "1"}])
    assert ps.cmd_pr(ok, 7, fallback_check="privacy-scan") == 0
    bad = FakeGH(checks=[{"name": "privacy-scan", "status": "completed", "conclusion": "failure", "completed_at": "1"}])
    assert ps.cmd_pr(bad, 7, fallback_check="privacy-scan") == 1
    none = FakeGH()
    assert ps.wait_check(none, "abc", "privacy-scan", timeout_s=0, sleep=lambda s: None) is False
    mail = "jane.doe" + "@" + "corp-mail.tw"
    hit = FakeGH(files=[{"filename": "b.md", "status": "added", "patch": f"@@ -0,0 +1 @@\n+mail {mail}\n"}])
    assert ps.cmd_pr(hit, 7, fallback_check="privacy-scan") == 1  # built-in hit fails before any waiting
    assert not any("/check-runs" in c[1] for c in hit.calls if c[0] == "get")


# ---- issues / comments -----------------------------------------------------
def test_comment_hit_is_minimized_and_labelled(rx, capsys):
    gh = FakeGH()
    ev = {"comment": {"body": "see ＺＯＲＢＬＡＴ", "node_id": "IC_1", "html_url": "u"}, "issue": {"number": 5}}
    assert ps.handle_event(gh, "issue_comment", ev, rx=rx) == "minimized"
    assert ("minimize", "IC_1") in gh.calls and ("label", 5) in gh.calls
    assert "zorblat" not in capsys.readouterr().out.lower()
    rc = {"comment": {"body": "qux frim", "node_id": "RC_1"}, "pull_request": {"number": 9}}
    assert ps.handle_event(gh, "pull_request_review_comment", rc, rx=rx) == "minimized"


def test_issue_body_is_masked_in_place(rx):
    gh = FakeGH()
    ev = {"issue": {"number": 3, "title": "ok", "body": "a zorblat b"}}
    assert ps.handle_event(gh, "issues", ev, rx=rx) == "masked"
    patch = next(c for c in gh.calls if c[0] == "patch")
    assert patch[1] == "/repos/o/r/issues/3" and patch[2]["body"] == "a *** b"
    pr = {"pull_request": {"number": 4, "title": "Qux Frim", "body": ""}}
    assert ps.handle_event(gh, "pull_request_target", pr, rx=rx) == "masked"
    assert ("patch", "/repos/o/r/pulls/4", {"title": "***", "body": ""}) in gh.calls


def test_clean_and_no_secret_do_nothing(rx, monkeypatch):
    gh = FakeGH()
    assert ps.handle_event(gh, "issues", {"issue": {"number": 1, "title": "t", "body": "b"}}, rx=rx) == "clean"
    monkeypatch.delenv("TLWU_DENYLIST_TEXT", raising=False)
    assert ps.handle_event(gh, "issues", {"issue": {"number": 1, "title": "zorblat", "body": ""}}) == "clean"
    assert gh.calls == []


def test_no_secret_issue_with_builtin_hit_is_masked(monkeypatch):
    monkeypatch.delenv("TLWU_DENYLIST_TEXT", raising=False)
    gh = FakeGH()
    phone = "09" + "12-345-678"
    ev = {"issue": {"number": 4, "title": "call me", "body": f"my phone {phone} thanks"}}
    assert ps.handle_event(gh, "issues", ev) == "masked"
    assert ("patch", "/repos/o/r/issues/4", {"title": "call me", "body": "my phone *** thanks"}) in gh.calls
    assert ("label", 4) in gh.calls


# ---- sweep -----------------------------------------------------------------
def test_sweep_counts_links_only(rx, tmp_path, capsys):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.txt").write_text("line\nZORBLAT\n", "utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.txt"], check=True)
    gh = FakeGH(issues=[{"number": 2, "title": "x", "body": "qux frim", "html_url": "i2"},
                        {"number": 3, "title": "y", "body": "", "html_url": "i3"}],
                comments=[{"body": "zorblat", "html_url": "c1", "issue_url": "https://api/x/issues/3"}],
                review_comments=[{"body": "fine", "html_url": "c2", "pull_request_url": "https://api/x/pulls/8"}])
    counts = ps.sweep(gh, rx, tmp_path)
    assert counts == {"files": 1, "items": 1, "comments": 1}
    assert ("label", 2) in gh.calls and ("label", 3) in gh.calls
    out = capsys.readouterr().out
    assert "file: a.txt:2" in out and "zorblat" not in out.lower() and "qux" not in out.lower()


def test_local_scan_masks_path_and_text(rx, tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "zorblat_notes.txt").write_text("ok\nQux  Frim\n", "utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    out: list[str] = []
    sd.scan_tracked(rx, out, tmp_path)
    assert len(out) == 2  # the file name and line 2
    assert all("zorblat" not in h.lower() and "qux" not in h.lower() for h in out)


def test_local_cli_empty_env_list_is_exit_2(monkeypatch):
    env = {**__import__("os").environ, "TLWU_DENYLIST_TEXT": "# only a comment\n", "TLWU_DENYLIST": "/nonexistent"}
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "scan_denylist.py"), "--no-staged", "--no-tracked"],
                       cwd=ROOT, capture_output=True, text=True, env=env)
    assert r.returncode == 2  # blank list text falls back to the file, which is missing


# ---- review follow-ups (CodeRabbit on #1407) ---------------------------------
def test_half_width_voiced_and_decomposed_accents():
    m = sd.Matcher(["ガゾル", "zélbo"])  # made-up words
    assert m.search("x ｶﾞｿﾞﾙ y")  # half-width kana with separate voiced marks
    assert m.sub("x ｶﾞｿﾞﾙ y") == "x ***** y"  # the marks are masked too
    assert m.search("ze\u0301lbo") and sd.Matcher(["ze\u0301lbo"]).search("zélbo")
    assert m.sub("ze\u0301lbo!") == "******!"


def test_added_line_starting_with_plus_plus_is_scanned(rx):
    patch = "@@ -0,0 +1,2 @@\n+++zorblat\n+ok\n"
    assert list(sd.added_lines(patch)) == [(1, "++zorblat"), (2, "ok")]
    diff = ("diff --git a/f.txt b/f.txt\n--- /dev/null\n+++ b/f.txt\n@@ -0,0 +1,2 @@\n"
            "+++ b/zorblat\n+x\n")
    assert list(sd._split_files(diff)) == [("f.txt", "@@ -0,0 +1,2 @@\n+++ b/zorblat\n+x\n")]


def test_line_separator_does_not_shift_lines():
    patch = "@@ -0,0 +1,2 @@\n+a\u2028b\n+c\n"
    assert list(sd.added_lines(patch)) == [(1, "a\u2028b"), (2, "c")]


def test_minimize_failure_is_an_error_and_label_comes_first(rx):
    class Failing(FakeGH):
        def minimize(self, node_id):
            raise RuntimeError("privacy-scan: minimizeComment failed; the comment is still visible")

    gh = Failing()
    ev = {"comment": {"body": "zorblat", "node_id": "IC_9"}, "issue": {"number": 6}}
    with pytest.raises(RuntimeError):
        ps.handle_event(gh, "issue_comment", ev, rx=rx)
    assert ("label", 6) in gh.calls


def test_real_client_checks_graphql_result(monkeypatch):
    g = ps.GitHub("o/r", token="t")
    monkeypatch.setattr(g, "_call", lambda *a, **k: ({"errors": [{"message": "no"}], "data": None}, ""))
    with pytest.raises(RuntimeError, match="still visible"):
        g.minimize("IC_1")
    monkeypatch.setattr(g, "_call", lambda *a, **k: (
        {"data": {"minimizeComment": {"minimizedComment": {"isMinimized": True}}}}, ""))
    assert g.minimize("IC_1")


def test_no_patch_file_name_is_url_quoted(rx):
    gh = FakeGH(files=[{"filename": "a dir/x#1?.bin", "status": "added"}], raw=b"zorblat\n")
    assert ps.cmd_pr(gh, 7, rx=rx) == 1
    raw = next(c[1] for c in gh.calls if c[0] == "raw")
    assert raw == "/repos/o/r/contents/a%20dir/x%231%3F.bin?ref=abc1234567"


def test_label_failure_still_minimizes_then_fails(rx):
    class LabelFails(FakeGH):
        def add_label(self, n):
            raise OSError("label request failed")

    gh = LabelFails()
    ev = {"comment": {"body": "zorblat", "node_id": "IC_7"}, "issue": {"number": 8}}
    with pytest.raises(RuntimeError, match="labelling failed"):
        ps.handle_event(gh, "issue_comment", ev, rx=rx)
    assert ("minimize", "IC_7") in gh.calls


# ---- built-in patterns (integrator scope change on #1407: no secret needed) ---------------------------------
# Every fake value is assembled at run time so this file's own added lines stay clean under the PR scan.
BUILTIN_HITS = {
    "email": ["write to " + "jane.doe" + "@" + "corp-mail.tw" + " today", "x " + "a.b+c" + "@" + "mail.example.io"],
    "phone TW": ["0912" + "345678", "tel 09" + "12-345-678", "09" + "12 345 678"],
    "phone +886": ["+886 " + "912 345 678", "+886-" + "2-2345-6789"],
    "phone E.164": ["+1 415 555 " + "2671", "call +44" + "2071838750"],
    "TW national ID": ["id A1" + "23456789", "F2" + "21234568"],
    "card": ["4111 1111 1111 " + "1111", "5555-5555-5555-" + "4444", "3782822463" + "10005"],
    "GitHub token": ["ghp_" + "Ab1" * 12, "github_pat_" + "B" * 30],
    "OpenAI-style key": ["key=sk-" + "proj-" + "x9" * 12, "sk-" + "Z" * 32],
    "AWS key": ["AKIA" + "IOSFODNN7EXAMPLE"],
    "Slack token": ["xox" + "b-123456789012-abcdefABCDEF"],
    "private key": ["-----BEGIN " + "RSA PRIVATE KEY-----", "-----BEGIN " + "PRIVATE KEY-----"],
}

BUILTIN_CLEAN = [
    "243713070+sunososobro-hub" + "@users.noreply.github.com",
    "noreply" + "@anthropic.com", "someone" + "@example.com", "dev" + "@sub.example.org", "git" + "@github.com:o/r.git",
    "react@18.3.1 and actions/checkout@v4.2.2", "@pytest.fixture", "sha 5ea3863a0912345678b1",
    "2026-11-17 12:00 +08:00", "UTC+8 2026 11 17", "ts 1700000000000 ms",  # 13-digit timestamp, not a card
    "4111 1111 1111 " + "1112",  # fails Luhn
    "A1" + "23456788",  # fails the ID checksum
    "phone 0812" + "345678",  # not a 09 mobile
    "sk-short", "ghp_short", "-----BEGIN " + "PUBLIC KEY-----",
]


@pytest.mark.parametrize("kind,text", [(k, t) for k, ts in BUILTIN_HITS.items() for t in ts])
def test_builtin_pattern_hits_are_masked(kind, text):
    m = ps.Combined()
    assert m.search(text), kind
    masked = m.sub(text)
    assert "*" in masked and len(masked) == len(text)
    digits = "".join(c for c in text if c.isdigit())
    if len(digits) >= 8:  # the number itself never survives the mask
        assert digits not in "".join(c for c in masked if c.isdigit())


@pytest.mark.parametrize("text", BUILTIN_CLEAN)
def test_builtin_patterns_leave_allowed_and_ordinary_text(text):
    assert not ps.Combined().search(text)


def test_combined_merges_list_words_and_builtin(monkeypatch):
    monkeypatch.setenv("TLWU_DENYLIST_TEXT", FAKE)
    m = ps.matcher()
    assert m.words is not None and m.size == len(ps.PATTERNS) + 2
    mail = "zorblat" + "@" + "corp-mail.tw"  # list word inside an email: one merged span
    assert m.sub(f"x {mail} y") == "x " + "*" * len(mail) + " y"
    assert m.sub("qux frim and 09" + "12345678", "***") == "*** and ***"
    monkeypatch.delenv("TLWU_DENYLIST_TEXT")
    assert ps.matcher().words is None and not ps.matcher().search("zorblat")


def test_sweep_without_secret_uses_builtin(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("TLWU_DENYLIST_TEXT", raising=False)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "n.txt").write_text("ok\nkey AKIA" + "IOSFODNN7EXAMPLE\n", "utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    counts = ps.sweep(FakeGH(), ps.matcher(), tmp_path)
    assert counts["files"] == 1
    assert "IOSFODNN7EXAMPLE" not in capsys.readouterr().out


# ---- review round 3 (CodeRabbit on ab003fe) ----------------------------------
JCB = "3566" + "002020360505"  # public JCB test number, split so this file passes the built-in card check
def test_private_key_block_masked_to_end_marker():
    pem = "PRIVATE " + "KEY-----"  # built at run time so this file does not trip the gate it tests
    body = ("before\n-----BEGIN RSA " + pem + "\nFAKEbase64lineONE\nFAKEbase64lineTWO\n"
            "-----END RSA " + pem + "\nafter")
    gh = FakeGH()
    assert ps.handle_event(gh, "issues", {"issue": {"number": 2, "title": "t", "body": body}},
                           rx=ps.Combined()) == "masked"
    new = next(c for c in gh.calls if c[0] == "patch")[2]["body"]
    assert new == "before\n***\nafter" and "FAKEbase64" not in new
    open_block = "x -----BEGIN " + "PRIVATE " + "KEY-----\nFAKEtail"
    assert ps.Combined().sub(open_block, "***") == "x ***"  # unterminated: masked to the end


def test_jcb_and_diners_card_numbers():
    rx = ps.Combined()
    assert rx.search("pay " + JCB + " now")  # JCB test number (Luhn-valid)
    assert rx.search("pay " + "3056 9309" + " 0259 04")  # Diners test number
    assert not rx.search("order 3566002020360506")  # Luhn fails


def test_api_key_with_underscore_before_hyphen():
    key = "sk-" + "proj_" + "A" * 10 + "-" + "B" * 30  # made-up shape, not a real key
    assert ps.Combined().sub(f"k={key};", "***") == "k=***;"


def test_fork_fallback_reads_status_on_head_and_target_posts_it():
    ok = FakeGH()
    ok.statuses = [{"context": "privacy-scan", "state": "success"}]
    assert ps.wait_check(ok, "abc", "privacy-scan", timeout_s=0, sleep=lambda s: None) is True
    bad = FakeGH()
    bad.statuses = [{"context": "privacy-scan", "state": "failure"}, {"context": "privacy-scan", "state": "success"}]
    assert ps.wait_check(bad, "abc", "privacy-scan", timeout_s=0, sleep=lambda s: None) is False
    gh = FakeGH(files=[{"filename": "a.py", "status": "added", "patch": "@@ -0,0 +1 @@\n+ok\n"}])
    assert ps.cmd_pr(gh, 7, rx=ps.Combined(), report_status="privacy-scan") == 0
    assert ("status", "abc1234567", "success", "privacy-scan") in gh.calls
    gh2 = FakeGH(files=[{"filename": "a.py", "status": "added", "patch": "@@ -0,0 +1 @@\n+pay " + JCB + "\n"}])
    assert ps.cmd_pr(gh2, 7, rx=ps.Combined(), report_status="privacy-scan") == 1
    assert ("status", "abc1234567", "failure", "privacy-scan") in gh2.calls


def test_private_key_end_label_must_match_begin():
    pem = "PRIVATE " + "KEY-----"
    text = ("a -----BEGIN RSA " + pem + "\n-----END EC " + pem + "\nFAKEmaterial\n-----END RSA " + pem
            + "\nb")
    assert ps.Combined().sub(text, "***") == "a ***\nb"
    stray = "a -----BEGIN RSA " + pem + "\n-----END EC " + pem + "\nFAKEmaterial"
    assert ps.Combined().sub(stray, "***") == "a ***"  # no matching END: masked to the end


def test_issue_body_with_mismatched_pem_markers_is_masked_in_place():
    pem = "PRIVATE " + "KEY-----"
    body = "a -----BEGIN RSA " + pem + "\n-----END EC " + pem + "\nFAKEmaterial\n-----END RSA " + pem + "\nb"
    gh = FakeGH()
    assert ps.handle_event(gh, "issues", {"issue": {"number": 4, "title": "t", "body": body}},
                           rx=ps.Combined()) == "masked"
    new = next(c for c in gh.calls if c[0] == "patch")[2]["body"]
    assert new == "a ***\nb" and "FAKEmaterial" not in new
    assert ("label", 4) in gh.calls


def test_status_goes_on_the_scanned_head_and_a_moved_head_gets_none():
    class Moving(FakeGH):
        heads = ["aaa1111111", "bbb2222222"]

        def get(self, path):
            if path.endswith("/pulls/7"):
                self.calls.append(("get", path))
                return {"head": {"sha": self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]}}
            return super().get(path)

    gh = Moving(files=[{"filename": "a.py", "status": "added", "patch": "@@ -0,0 +1 @@\n+ok\n"}])
    assert ps.cmd_pr(gh, 7, rx=ps.Combined(), report_status="privacy-scan") == 1
    assert not [c for c in gh.calls if c[0] == "status"]  # no success published on the newer head
    gh2 = FakeGH(files=[{"filename": "a.py", "status": "added", "patch": "@@ -0,0 +1 @@\n+ok\n"}])
    assert ps.cmd_pr(gh2, 7, rx=ps.Combined(), report_status="privacy-scan") == 0
    assert [c for c in gh2.calls if c[0] == "status"] == [("status", "abc1234567", "success", "privacy-scan")]


# ---- recent-comment scan (#1498) ---------------------------------------------
def test_recent_scan_hides_hits_and_labels(rx, capsys):
    gh = FakeGH(comments=[{"body": "zorblat", "html_url": "c1", "node_id": "N1", "issue_url": "https://api/x/issues/3"},
                          {"body": "fine", "html_url": "c2", "node_id": "N2", "issue_url": "https://api/x/issues/4"}],
                review_comments=[{"body": "qux frim", "html_url": "c3", "node_id": "N3", "pull_request_url": "https://api/x/pulls/8"}])
    counts = ps.scan_recent(gh, rx, "2026-11-18T00:00:00Z")
    assert counts == {"comments": 3, "hidden": 2}
    assert ("minimize", "N1") in gh.calls and ("minimize", "N3") in gh.calls and ("minimize", "N2") not in gh.calls
    assert ("label", 3) in gh.calls and ("label", 8) in gh.calls
    out = capsys.readouterr().out
    assert "zorblat" not in out.lower() and "qux" not in out.lower()


def test_recent_scan_passes_since_to_the_api(rx):
    seen = []

    class G(FakeGH):
        def paginate(self, path):
            seen.append(path)
            return iter(())

    ps.scan_recent(G(), rx, "2026-11-18T01:02:03Z")
    assert all(p.endswith("?since=2026-11-18T01:02:03Z") for p in seen) and len(seen) == 2



def test_recent_scan_continues_after_failures_then_fails(rx):
    class G(FakeGH):
        def add_label(self, n):
            if n == 3:
                raise RuntimeError("label boom")
            super().add_label(n)

        def minimize(self, node_id):
            if node_id == "N2":
                raise RuntimeError("gone")
            super().minimize(node_id)

    gh = G(comments=[{"body": "zorblat", "html_url": "c1", "node_id": "N1", "issue_url": "https://api/x/issues/3"},
                     {"body": "zorblat", "html_url": "c2", "node_id": "N2", "issue_url": "https://api/x/issues/4"}],
           review_comments=[{"body": "qux frim", "html_url": "c3", "node_id": "N3", "pull_request_url": "https://api/x/pulls/8"}])
    with pytest.raises(RuntimeError) as e:
        ps.scan_recent(gh, rx, "2026-11-18T00:00:00Z")
    assert ("minimize", "N1") in gh.calls and ("minimize", "N3") in gh.calls  # label failure still hides; scan went on
    assert "label c1" in str(e.value) and "hide c2" in str(e.value) and "zorblat" not in str(e.value).lower()


def test_recent_scan_attempts_second_feed_when_first_pagination_fails(rx):
    class G(FakeGH):
        def paginate(self, path):
            if path.split("?")[0].endswith("/issues/comments"):
                raise RuntimeError("timeout")
            return super().paginate(path)

    gh = G(review_comments=[{"body": "qux frim", "html_url": "c3", "node_id": "N3", "pull_request_url": "https://api/x/pulls/8"}])
    with pytest.raises(RuntimeError, match="feed"):
        ps.scan_recent(gh, rx, "2026-11-18T00:00:00Z")
    assert ("minimize", "N3") in gh.calls
