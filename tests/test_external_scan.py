"""scripts/external_scan.py (#360): recorded API JSON through an injected fetch, no network."""
import json
import subprocess

import external_scan as es
import pytest

R, O = "o/r", "o"
U = lambda n: {"login": n, "type": "User"}
BOT = lambda n: {"login": n, "type": "Bot"}


def commit(sha, subject, date, name="x", parents=()):
    return {"sha": sha, "commit": {"message": subject + "\n\nbody", "author": {"name": name, "date": date}}}


# upstream history (after a rewrite: new SHAs, same author time/name/subject)
UP = [commit("u2", "fix #9: thing (#10)", "2026-11-11T20:00:00+08:00", "o"),
      commit("u1", "Merge pull request #1", "2026-11-10T23:38:24+08:00", "o")]


class API:
    def __init__(self, extra=None):
        self.calls = []
        self.t = {
            f"repos/{R}/issues?state=open&per_page=100": [
                {"number": 1, "title": "outsider bug", "user": U("bob")},
                {"number": 2, "title": "mine", "user": U(O)},
                {"number": 3, "title": "pr as issue", "user": U("carl"), "pull_request": {}},
                {"number": 4, "title": "bot issue", "user": BOT("renovate[bot]")},
            ],
            f"repos/{R}/pulls?state=open&per_page=100": [
                {"number": 3, "title": "ext pr", "user": U("carl")},
                {"number": 5, "title": "my pr", "user": U(O)},
                {"number": 6, "title": "deps", "user": BOT("dependabot[bot]")},
            ],
            f"repos/{R}/issues/comments?per_page=100": [
                {"issue_url": f"https://api.github.com/repos/{R}/issues/1", "created_at": "2026-11-01T00:00:00Z", "user": U("bob")},
                {"issue_url": f"https://api.github.com/repos/{R}/issues/1", "created_at": "2026-11-01T01:00:00Z", "user": U(O)},
                {"issue_url": f"https://api.github.com/repos/{R}/issues/2", "created_at": "2026-11-01T00:00:00Z", "user": U(O)},
                {"issue_url": f"https://api.github.com/repos/{R}/issues/2", "created_at": "2026-11-01T02:00:00Z", "user": U("dan")},
                {"issue_url": f"https://api.github.com/repos/{R}/issues/7", "created_at": "2026-11-01T00:00:00Z", "user": BOT("coderabbitai[bot]")},
                {"issue_url": f"https://api.github.com/repos/{R}/issues/8", "created_at": "2026-11-02T00:00:00Z", "user": U("eve")},
            ],
            f"repos/{R}/pulls/comments?per_page=100": [
                {"pull_request_url": f"https://api.github.com/repos/{R}/pulls/3", "created_at": "2026-11-01T00:00:00Z", "user": U(O)},
                {"pull_request_url": f"https://api.github.com/repos/{R}/pulls/3", "created_at": "2026-11-01T05:00:00Z", "user": U("carl")},
            ],
            f"repos/{R}": {"default_branch": "main"},
            f"repos/{R}/forks?per_page=100": [{"full_name": f"{x}/r"} for x in "abcdefg"],
            # a: ahead by 4 vs main, by 3 vs dev -> 3 commits in neither
            "repos/a/r/branches?per_page=100": [{"name": "main", "commit": {"sha": "aaa"}}],
            f"repos/{R}/compare/main...aaa?per_page=1": {"ahead_by": 4},
            f"repos/{R}/compare/dev...aaa?per_page=1": {"ahead_by": 3},
            # b: even
            "repos/b/r/branches?per_page=100": [{"name": "main", "commit": {"sha": "bbb"}}],
            f"repos/{R}/compare/main...bbb?per_page=1": {"ahead_by": 0},
            f"repos/{R}/compare/dev...bbb?per_page=1": {"ahead_by": 2},
            # c: gh cannot read the fork -> git refs fallback, branch ahead by 1
            "repos/c/r/branches?per_page=100": es.FetchError("gh: access not enabled (HTTP 403)"),
            f"repos/{R}/compare/main...ccc?per_page=1": {"ahead_by": 1},
            f"repos/{R}/compare/dev...ccc?per_page=1": {"ahead_by": 1},
            # d: fork unreadable both ways -> reported, not dropped
            "repos/d/r/branches?per_page=100": es.FetchError("gh: Not Found (HTTP 404)"),
            # e: compare fails for another reason -> reported with the reason
            "repos/e/r/branches?per_page=100": [{"name": "main", "commit": {"sha": "eee"}}],
            f"repos/{R}/compare/main...eee?per_page=1": es.FetchError("gh: Server Error (HTTP 502)"),
            f"repos/{R}/compare/dev...eee?per_page=1": es.FetchError("gh: Server Error (HTTP 502)"),
            # f: upstream history was rewritten; fork has 1 own commit on top of a shared one
            "repos/f/r/branches?per_page=100": [{"name": "main", "commit": {"sha": "fff"}}],
            f"repos/{R}/compare/main...fff?per_page=1": es.FetchError("gh: No common ancestor between main and fff. (HTTP 404)"),
            f"repos/{R}/compare/dev...fff?per_page=1": es.FetchError("gh: No common ancestor between dev and fff. (HTTP 404)"),
            f"repos/{R}/commits?sha=fff&per_page=100&page=1": [
                commit("fff", "Updated the gitignore file", "2026-11-12T10:00:00Z", "jerry"),
                commit("f1", "fix #9: thing (#10)", "2026-11-11T12:00:00Z", "Alonso"),  # == u2: same instant, other tz, renamed author
                commit("f0", "Merge pull request #1", "2026-11-10T15:38:24Z", "o")],
            # g: rewritten, nothing shared
            "repos/g/r/branches?per_page=100": [{"name": "main", "commit": {"sha": "ggg"}}],
            f"repos/{R}/compare/main...ggg?per_page=1": es.FetchError("gh: No common ancestor between main and ggg. (HTTP 404)"),
            f"repos/{R}/compare/dev...ggg?per_page=1": es.FetchError("gh: No common ancestor between dev and ggg. (HTTP 404)"),
            f"repos/{R}/commits?sha=ggg&per_page=100&page=1": [
                commit("ggg", "old menu tweak", "2026-11-09T00:00:00Z", "pm"),
                commit("g0", "initial", "2026-11-08T00:00:00Z", "o")],
            f"repos/{R}/commits?sha=main&per_page=100": UP,
            f"repos/{R}/commits?sha=dev&per_page=100": UP[:1],
        }
        self.t.update(extra or {})

    def __call__(self, path, paginate=False):
        self.calls.append(path)
        v = self.t[path]
        if isinstance(v, Exception):
            raise v
        return [v] if paginate else v


def refs(full_name):
    if full_name == "c/r":
        return {"main": "ccc"}
    raise OSError("HTTP Error 404: Not Found")


def run(api=None):
    return es.render(es.scan(api or API(), repo=R, owner=O, refs=refs))


def section(out, title):
    body = out.split(f"== {title}")[1].split("\n== ")[0]
    return body.splitlines()[1:]


def test_four_sections_issues_prs_comments():
    out = run()
    assert out.count("\n== ") == 3 and out.startswith("== ")
    assert [line for line in section(out, "Open non-owner issues") if line] == ["#1 outsider bug (@bob)"]
    assert [line for line in section(out, "Open non-owner PRs") if line] == ["#3 ext pr (@carl)"]
    com = [line for line in section(out, "Non-owner comments") if line]
    # #1 answered by owner, #7 bot only -> not listed; #2 dan after owner, #3 review comment, #8 never answered
    assert [c.split(" by ")[0] for c in com] == ["#2", "#3", "#8"]
    assert "@carl" in com[1] and "@eve" in com[2]


def test_forks_ahead_even_and_failures_are_all_reported():
    forks = section(run(), "Forks with commits not in upstream")
    text = "\n".join(forks)
    assert "a/r:main ahead by 3" in text  # min over main/dev
    assert "b/r" not in "\n".join(forks[:-1])  # even fork is not in the ahead list ...
    assert "even with upstream: b/r" in forks[-1]  # ... but named in the summary
    assert "c/r:main ahead by 1" in text  # branch heads from git refs when gh cannot read the fork
    assert "d/r check failed: branches: gh: Not Found (HTTP 404); git refs: HTTP Error 404: Not Found" in text
    assert "e/r:main check failed: main: gh: Server Error (HTTP 502); dev: gh: Server Error (HTTP 502)" in text
    assert "f/r:main ahead by 1" in text  # rewritten upstream: matched by author time + subject
    assert "g/r:main unrelated history: 2+ commits, no commit shared with upstream, head ggg old menu tweak" in text
    assert "7 forks checked" in forks[-1]


def test_one_fork_raising_does_not_hide_the_others():
    api = API({f"repos/{R}/compare/main...aaa?per_page=1": RuntimeError("boom")})
    text = "\n".join(section(run(api), "Forks with commits not in upstream"))
    assert "a/r:main ahead by 3" in text  # dev compare still worked
    api = API({f"repos/{R}/compare/main...aaa?per_page=1": RuntimeError("boom"),
               f"repos/{R}/compare/dev...aaa?per_page=1": RuntimeError("boom2")})
    text = "\n".join(section(run(api), "Forks with commits not in upstream"))
    assert "a/r:main check failed: main: boom; dev: boom2" in text
    assert "c/r:main ahead by 1" in text and "f/r:main ahead by 1" in text


def test_upstream_history_fetched_only_when_needed():
    api = API()
    api.t[f"repos/{R}/forks?per_page=100"] = [{"full_name": "a/r"}, {"full_name": "b/r"}]
    run(api)
    assert not any("/commits?sha=main" in c for c in api.calls)


def test_only_get_requests(monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        page = int(cmd[2].rsplit("page=", 1)[1]) if "&page=" in cmd[2] else 0
        items = [{"n": i} for i in range(2 if page == 1 else 1 if page == 2 else 0)]
        return subprocess.CompletedProcess(cmd, 0, json.dumps(items if page else {"x": 1}), "")

    monkeypatch.setattr(es.subprocess, "run", fake_run)
    assert es.gh_fetch("repos/o/r") == {"x": 1}
    assert es.gh_fetch("repos/o/r/forks?per_page=2", paginate=True) == [[{"n": 0}, {"n": 1}], [{"n": 0}]]
    assert all(c[:2] == ["gh", "api"] and len(c) == 3 for c in seen)  # no -X / -f / --method
    assert [c[2] for c in seen][1:] == ["repos/o/r/forks?per_page=2&page=1", "repos/o/r/forks?per_page=2&page=2"]


def test_gh_error_message_is_kept(monkeypatch):
    monkeypatch.setattr(es.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, "", "gh: No common ancestor between main and x. (HTTP 404)\n"))
    with pytest.raises(es.FetchError, match="No common ancestor"):
        es.gh_fetch("repos/o/r/compare/main...x")


def test_parse_refs_pkt_lines():
    adv = ("001e# service=git-upload-pack\n0000"
           "0155" + "a" * 40 + " HEAD\x00multi_ack symref=HEAD:refs/heads/main\n"
           "003f" + "a" * 40 + " refs/heads/main\n"
           "0048" + "b" * 40 + " refs/heads/fix/40-x\n"
           "003e" + "c" * 40 + " refs/tags/v1\n0000")
    assert es.parse_refs(adv) == {"main": "a" * 40, "fix/40-x": "b" * 40}
