"""scripts/external_scan.py (#360): recorded API JSON through an injected fetch, no network."""
import json
import subprocess

import external_scan as es
import pytest

R, O = "o/r", "o"
U = lambda n: {"login": n, "type": "User"}
BOT = lambda n: {"login": n, "type": "Bot"}


def commit(sha, subject, date, name="x"):
    return {"sha": sha, "commit": {"message": subject + "\n\nbody", "author": {"name": name, "date": date}}}


# upstream: main has u2 (main only) and u1; dev has u3 (dev only) and u1
U1 = commit("u1", "Merge pull request #1", "2026-11-10T23:38:24+08:00", "o")
U2 = commit("u2", "fix #9: thing (#10)", "2026-11-11T20:00:00+08:00", "o")
U3 = commit("u3", "fix #11: other (#12)", "2026-11-12T08:00:00+08:00", "o")
OWN = lambda sha, subj: commit(sha, subj, "2026-11-12T10:00:00Z", "jerry")


def hist(sha):
    return f"repos/{R}/commits?sha={sha}&per_page=100&page=1"


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
            f"repos/{R}/pulls?state=all&per_page=100": [
                {"number": 3, "user": U("carl")}, {"number": 5, "user": U(O)}, {"number": 9, "user": U("zed")}],
            f"repos/{R}/pulls/3/reviews?per_page=100": [
                {"body": "", "submitted_at": "2026-11-01T06:00:00Z", "user": U("carl")}],
            # owner's open PR: outsider review body, then owner replies with a review body -> answered
            f"repos/{R}/pulls/5/reviews?per_page=100": [
                {"body": "looks off", "submitted_at": "2026-11-03T00:00:00Z", "user": U("amy")},
                {"body": "fixed", "submitted_at": "2026-11-03T01:00:00Z", "user": U(O)}],
            f"repos/{R}/pulls/6/reviews?per_page=100": [],
            # closed outsider PR whose only activity is a review body -> unanswered
            f"repos/{R}/pulls/9/reviews?per_page=100": [
                {"body": "please look", "submitted_at": "2026-11-04T00:00:00Z", "user": U("zed")},
                {"body": "pending", "submitted_at": None, "user": U("zed")}],
            f"repos/{R}": {"default_branch": "main"},
            f"repos/{R}/forks?per_page=100": [{"full_name": f"{x}/r"} for x in "abcdefghm"],
            f"repos/{R}/commits?sha=main&per_page=100": [U2, U1],
            f"repos/{R}/commits?sha=dev&per_page=100": [U3, U1],
            # a: own commit a1 under its head, on top of upstream SHAs -> ahead 2
            "repos/a/r/branches?per_page=100": [{"name": "main", "commit": {"sha": "aaa"}}],
            hist("aaa"): [OWN("aaa", "head"), OWN("a1", "own"), U2, U1],
            # b: head is an upstream commit -> even, history not walked
            "repos/b/r/branches?per_page=100": [{"name": "main", "commit": {"sha": "u2"}}],
            # c: gh cannot read the fork -> git refs fallback
            "repos/c/r/branches?per_page=100": es.FetchError("gh: access not enabled (HTTP 403)"),
            hist("ccc"): [OWN("ccc", "c own"), U1],
            # d: fork unreadable both ways -> reported, not dropped
            "repos/d/r/branches?per_page=100": es.FetchError("gh: Not Found (HTTP 404)"),
            # e: history walk fails -> reported with the reason
            "repos/e/r/branches?per_page=100": [{"name": "main", "commit": {"sha": "eee"}}],
            hist("eee"): es.FetchError("gh: Server Error (HTTP 502)"),
            # f: forked before the upstream rewrite: same author instant (other tz) + subject, new SHAs/author
            "repos/f/r/branches?per_page=100": [{"name": "main", "commit": {"sha": "fff"}}],
            hist("fff"): [OWN("fff", "Updated the gitignore file"),
                          commit("f2", "fix #9: thing (#10)", "2026-11-11T12:00:00Z", "Alonso"),
                          commit("f1", "Merge pull request #1", "2026-11-10T15:38:24Z", "Alonso")],
            # g: nothing shared
            "repos/g/r/branches?per_page=100": [{"name": "main", "commit": {"sha": "ggg"}}],
            hist("ggg"): [OWN("ggg", "old menu tweak"), OWN("g0", "initial")],
            # h: head matches upstream u3 by key, but its parent h1 is an outside change -> ahead 1
            "repos/h/r/branches?per_page=100": [{"name": "main", "commit": {"sha": "hhh"}}],
            hist("hhh"): [commit("hhh", "fix #11: other (#12)", "2026-11-12T00:00:00Z"), OWN("h1", "outside"), U1],
            # m: merge of main-only u2 and dev-only u3 -> only the merge itself is not upstream
            "repos/m/r/branches?per_page=100": [{"name": "main", "commit": {"sha": "mmm"}}],
            hist("mmm"): [OWN("mmm", "Merge main and dev"), U3, U2, U1],
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
    # #1 answered by owner, #7 bot only, #5 answered by an owner review body -> not listed
    # #2 dan after owner, #3 review comment, #8 never answered, #9 review body only
    assert [c.split(" by ")[0] for c in com] == ["#2", "#3", "#8", "#9"]
    assert "@carl" in com[1] and "@eve" in com[2] and "@zed (last 2026-11-04T00:00:00Z)" in com[3]


def test_forks_ahead_even_and_failures_are_all_reported():
    forks = section(run(), "Forks with commits not in upstream")
    text = "\n".join(forks[:-1])
    assert "a/r:main ahead by 2" in text  # every unshared commit, not just the head
    assert "b/r" not in text and "even with upstream: b/r" in forks[-1]
    assert "c/r:main ahead by 1" in text  # branch heads from git refs when gh cannot read the fork
    assert "d/r check failed: branches: gh: Not Found (HTTP 404); git refs: HTTP Error 404: Not Found" in text
    assert "e/r:main check failed: history walk failed: gh: Server Error (HTTP 502)" in text
    assert "f/r:main ahead by 1" in text  # rewritten upstream: matched by author instant + subject
    assert "g/r:main unrelated history: 2 commits, none shared with upstream, head ggg old menu tweak" in text
    assert "h/r:main ahead by 1" in text  # a shared head does not hide an outside ancestor
    assert "m/r:main ahead by 1" in text  # union of main and dev, not min of per-base counts
    assert "9 forks checked" in forks[-1]


def test_one_fork_raising_does_not_hide_the_others():
    api = API({hist("aaa"): RuntimeError("boom")})
    text = "\n".join(section(run(api), "Forks with commits not in upstream"))
    assert "a/r:main check failed: history walk failed: boom" in text
    assert "c/r:main ahead by 1" in text and "f/r:main ahead by 1" in text


def test_upstream_read_failure_is_reported_per_fork():
    api = API({f"repos/{R}/commits?sha=dev&per_page=100": es.FetchError("gh: Bad credentials (HTTP 401)")})
    text = "\n".join(section(run(api), "Forks with commits not in upstream"))
    assert "a/r check failed: gh: Bad credentials (HTTP 401)" in text
    assert "ahead by" not in text


def test_long_history_is_not_reported_as_checked():
    api = API({hist("aaa"): [OWN(f"x{i}", f"c{i}") for i in range(100)],
               f"repos/{R}/commits?sha=aaa&per_page=100&page=2": [OWN(f"y{i}", f"d{i}") for i in range(100)]})
    up = es.upstream_commits(api, R, ["main", "dev"])
    assert es.check_branch(api, R, "aaa", up, max_pages=2) == (
        "error", "history longer than 200 commits, not fully checked (200+ not upstream)")


def test_upstream_history_fetched_only_when_needed():
    api = API()
    api.t[f"repos/{R}/forks?per_page=100"] = [{"full_name": "d/r"}]
    run(api)
    assert not any("/commits?sha=" in c for c in api.calls)


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
