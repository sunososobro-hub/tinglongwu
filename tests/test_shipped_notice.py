"""Behaviour tests for scripts/shipped_notice.py selection logic (#1504)."""
import importlib.util
import pathlib
import subprocess

spec = importlib.util.spec_from_file_location("shipped_notice", pathlib.Path(__file__).parent.parent / "scripts" / "shipped_notice.py")
sn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sn)


def _git(cwd, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *a], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout.strip()


def test_is_ancestor(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "a")
    fix = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "tag", "v0.1.0")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "b")
    late = _git(tmp_path, "rev-parse", "HEAD")
    assert sn.is_ancestor(fix, "v0.1.0", cwd=str(tmp_path))
    assert not sn.is_ancestor(late, "v0.1.0", cwd=str(tmp_path))


def test_issues_from_body():
    assert sn.issues_from_body("Closes #5\nOwner comments: none\nRefs #7, closes #5") == [5, 7]
    assert sn.issues_from_body(None) == []


def test_decide_prerelease_vs_final_and_dedup():
    assert sn.decide("v1.0.0-rc1", True, True, set()) == "Shipped in v1.0.0-rc1 (pre-release)"
    assert sn.decide("v1.0.0-rc2", True, True, {"pre"}) is None
    assert sn.decide("v1.0.0", False, True, {"pre"}) == "Shipped in v1.0.0"
    assert sn.decide("v1.0.1", False, True, {"final"}) is None
    assert sn.decide("v1.0.0", False, False, set()) is None


def test_shipped_kinds_parses_existing_comments():
    got = sn.shipped_kinds(["hello", "Shipped in v0.4.7 (pre-release)", "Shipped in v0.4.8"])
    assert got == {"pre", "final"}


def test_is_ancestor_reports_git_errors(tmp_path):
    import pytest
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "a")
    with pytest.raises(RuntimeError):
        sn.is_ancestor("HEAD", "no-such-tag", cwd=str(tmp_path))


def test_is_ancestor_missing_commit_is_not_contained(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "a")
    _git(tmp_path, "tag", "v0.1.0")
    assert sn.is_ancestor("0" * 40, "v0.1.0", cwd=str(tmp_path)) is False


def test_workflow_runs_on_schedule_and_dispatch_without_issues():
    import pathlib
    import yaml

    wf = yaml.safe_load(pathlib.Path(".github/workflows/shipped-notice.yml").read_text())
    on = wf.get("on", wf.get(True))
    assert on["schedule"][0]["cron"]
    assert on["workflow_dispatch"]["inputs"]["issues"]["required"] is False


RELS = [  # GitHub order: newest first
    {"tag_name": "v0.4.10", "prerelease": False},
    {"tag_name": "v0.4.10-rc1", "prerelease": True},
    {"tag_name": "v0.4.9", "prerelease": False},
    {"tag_name": "v0.4.9-rc1", "prerelease": True},
    {"tag_name": "v0.4.8-draft", "prerelease": False, "draft": True},
]


def test_previous_tag_final_skips_prereleases_so_their_fixes_get_a_final_note():
    assert sn.previous_tag(RELS, "v0.4.10") == "v0.4.9"
    assert sn.previous_tag(RELS, "v0.4.10-rc1") == "v0.4.9"
    assert sn.previous_tag(RELS, "v0.4.9-rc1") is None
    assert sn.previous_tag(RELS, "v9") is None


def test_unannounced_tags_includes_prereleases_oldest_first_and_drops_drafts():
    assert sn.unannounced_tags(RELS) == ["v0.4.9-rc1", "v0.4.9", "v0.4.10-rc1", "v0.4.10"]
    assert sn.unannounced_tags(RELS, limit=2) == ["v0.4.10-rc1", "v0.4.10"]


def test_cli_needs_exactly_one_of_tag_or_all():
    import pytest
    with pytest.raises(SystemExit):
        sn.main([])
    with pytest.raises(SystemExit):
        sn.main(["--tag", "v1", "--all"])


def test_issues_in_range_explicit_none_and_missing_prev_scan_whole_history(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "first (#1)")
    _git(tmp_path, "tag", "v0.1.0")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "second (#2)")
    _git(tmp_path, "tag", "v0.2.0")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sn, "_api", lambda path, token, *a, **k: {"body": "Closes #" + path.rsplit("/", 1)[1]})
    assert sn.issues_in_range("r", "v0.2.0", "t") == [2]  # omitted: nearest earlier tag
    assert sn.issues_in_range("r", "v0.2.0", "t", "v0.1.0") == [2]  # bounded
    assert sn.issues_in_range("r", "v0.2.0", "t", None) == [2, 1]  # explicit None: whole history
    assert sn.issues_in_range("r", "v0.2.0", "t", "v0.0.0-gone") == [2, 1]  # missing ref: no abort


def test_issues_in_range_skips_subject_numbers_that_are_not_prs(tmp_path, monkeypatch):
    """#1504: a `(#N)` in a commit subject can be an issue; /pulls/N then 404s and used to abort the whole run."""
    import io
    import urllib.error
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "real pr (#1)")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "mentions an issue (#2)")
    _git(tmp_path, "tag", "v0.1.0")
    monkeypatch.chdir(tmp_path)

    def fake_api(path, token, *a, **k):
        n = path.rsplit("/", 1)[1]
        if n == "2":
            raise urllib.error.HTTPError(path, 404, "Not Found", {}, io.BytesIO(b""))
        if n == "3":
            raise urllib.error.HTTPError(path, 500, "boom", {}, io.BytesIO(b""))
        return {"body": "Closes #" + n}

    monkeypatch.setattr(sn, "_api", fake_api)
    assert sn.issues_in_range("r", "v0.1.0", "t", None) == [1]
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "server error (#3)")
    _git(tmp_path, "tag", "v0.2.0")
    assert sn.issues_in_range("r", "v0.2.0", "t", "v0.1.0") == []  # 5xx is skipped since #2048


def test_issues_in_range_skips_410_and_422_and_keeps_later_prs(tmp_path, monkeypatch, capsys):
    """#2037: HTTP 422 on /pulls/N aborted the daily run; 404/410/422 skip one line, 401/403 still raise."""
    import io
    import urllib.error
    import pytest
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "gone (#10)")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "unprocessable (#11)")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "real pr (#12)")
    _git(tmp_path, "tag", "v0.1.0")
    monkeypatch.chdir(tmp_path)
    codes = {"10": 410, "11": 422}

    def fake_api(path, token, *a, **k):
        n = path.rsplit("/", 1)[1]
        if n in codes:
            raise urllib.error.HTTPError(path, codes[n], "x", {}, io.BytesIO(b""))
        return {"body": "Closes #" + n}

    monkeypatch.setattr(sn, "_api", fake_api)
    assert sn.issues_in_range("r", "v0.1.0", "t", None) == [12]
    out = capsys.readouterr().out
    assert "#10 skipped (HTTP 410" in out and "#11 skipped (HTTP 422" in out
    for bad in (401, 403):
        codes["11"] = bad
        with pytest.raises(urllib.error.HTTPError):
            sn.issues_in_range("r", "v0.1.0", "t", None)


def _http_error(code):
    import io
    import urllib.error
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b""))


class _Resp:
    def __init__(self, body):
        self._b = body

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_api_retries_get_once_after_5xx(monkeypatch):
    """#2048: urlopen returns 500 once, then a PR body: the GET succeeds."""
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(500)
        return _Resp(b'{"body": "Closes #7"}')

    monkeypatch.setattr(sn.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sn.time, "sleep", lambda s: None)
    assert sn._api("/repos/r/pulls/1", "t") == {"body": "Closes #7"}
    assert len(calls) == 2


def test_api_does_not_retry_4xx_or_post(monkeypatch):
    import pytest
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append(1)
        raise _http_error(404 if len(calls) == 1 else 500)

    monkeypatch.setattr(sn.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sn.time, "sleep", lambda s: None)
    with pytest.raises(sn.urllib.error.HTTPError):
        sn._api("/x", "t")
    assert len(calls) == 1
    with pytest.raises(sn.urllib.error.HTTPError):
        sn._api("/x", "t", "POST", {"a": 1})
    assert len(calls) == 2


def test_issues_in_range_continues_after_5xx_twice(tmp_path, monkeypatch, capsys):
    """#2048: 500 on both tries for one PR lookup skips that commit, the other commits still count."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "first (#20)")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "flaky (#21)")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "last (#22)")
    _git(tmp_path, "tag", "v0.1.0")
    monkeypatch.chdir(tmp_path)

    def fake_urlopen(req, timeout=0):
        if req.full_url.endswith("/pulls/21"):
            raise _http_error(500)
        return _Resp(('{"body": "Closes #%s"}' % req.full_url.rsplit("/", 1)[1]).encode())

    monkeypatch.setattr(sn.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sn.time, "sleep", lambda s: None)
    assert sn.issues_in_range("r", "v0.1.0", "t", None) == [22, 20]
    assert "'flaky (#21)': #21 skipped (HTTP 500" in capsys.readouterr().out


def _fake_env(monkeypatch, comments):
    calls = []

    def fake_api(path, token, method="GET", data=None):
        calls.append((method, path))
        return {"state": "closed", "user": {"login": "x"}}

    monkeypatch.setattr(sn, "_api", fake_api)
    monkeypatch.setattr(sn, "_paged", lambda path, token: (calls.append(("GET", path)), comments)[1])
    return calls


def test_notify_tag_skips_timeline_when_already_noted(monkeypatch, capsys):
    done = [{"body": "Shipped in v1.0.0", "user": {"login": "github-actions[bot]"}}]
    calls = _fake_env(monkeypatch, done)
    assert sn.notify_tag("o/r", "t", "v1.0.0", False, [5], False) is None
    assert not any("timeline" in p or "/pulls/" in p for _, p in calls)
    assert "#5: already noted, skipped" in capsys.readouterr().out


def test_notify_tag_still_looks_up_fix_when_not_noted(monkeypatch):
    calls = _fake_env(monkeypatch, [])
    monkeypatch.setattr(sn, "fix_commits", lambda *a: calls.append(("GET", "fix_commits")) or [])
    sn.notify_tag("o/r", "t", "v1.0.0", False, [5], False)
    assert ("GET", "fix_commits") in calls


def test_notify_tag_budget_stops_with_last_issue(monkeypatch):
    _fake_env(monkeypatch, [{"body": "Shipped in v1.0.0", "user": {"login": "github-actions[bot]"}}])
    ticks = iter([0.0, 20.0])
    monkeypatch.setattr(sn.time, "monotonic", lambda: next(ticks))
    assert sn.notify_tag("o/r", "t", "v1.0.0", False, [5, 6, 7], False, deadline=10.0) == 5


def test_main_budget_prints_final_line_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(sn, "_api", lambda *a, **k: [{"tag_name": "v1.0.0", "prerelease": False}])
    monkeypatch.setattr(sn, "_have_tag", lambda t: True)
    monkeypatch.setattr(sn, "issues_in_range", lambda *a, **k: [5, 6])
    monkeypatch.setattr(sn, "notify_tag", lambda *a, **k: 5)
    assert sn.main(["--all", "--budget-sec", "0.001"]) == 0
    assert capsys.readouterr().out.strip().endswith("budget reached after v1.0.0 #5")


def test_main_sets_line_buffering(monkeypatch):
    seen = []
    monkeypatch.setattr(sn.sys.stdout, "reconfigure", lambda **k: seen.append(k), raising=False)
    monkeypatch.setattr(sn, "_api", lambda *a, **k: [])
    sn.main(["--all"])
    assert {"line_buffering": True} in seen
