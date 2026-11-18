"""#944: a PR commit by a placeholder identity (`x <x@x>`) fails the check; normal worker / fork PRs pass."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "commit_identity.py"
sys.path.insert(0, str(ROOT / "scripts"))
import commit_identity as ci  # noqa: E402

OWNER = ("sunososobro-hub", "243713070+sunososobro-hub@users.noreply.github.com")


def commit(sha, author, committer=None, message="fix"):
    committer = committer or author
    return {"sha": sha, "commit": {"author": {"name": author[0], "email": author[1]},
                                   "committer": {"name": committer[0], "email": committer[1]}, "message": message}}


def run(tmp_path, commits, *args):
    f = tmp_path / "commits.json"
    f.write_text(json.dumps(commits), "utf-8")
    return subprocess.run([sys.executable, str(SCRIPT), str(f), *args], capture_output=True, text=True)


def test_placeholder_identity_fails_and_names_the_commit(tmp_path):
    r = run(tmp_path, [commit("aaaaaaa1", OWNER), commit("99f27220deadbeef", ("x", "x@x"))])
    assert r.returncode == 1
    assert "99f2722" in r.stdout and "<x@x>" in r.stdout
    assert "aaaaaaa" not in r.stdout


@pytest.mark.parametrize("ident,committer", [
    (OWNER, None),
    (("sunososobro-hub", "sunososobro@gmail.com"), ("GitHub", "noreply@github.com")),  # web-flow merge
    (("Claude", "noreply@anthropic.com"), None),
    (("tlwu", "noreply@anthropic.com"), None),
    (("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com"), None),
])
def test_known_worker_identities_pass(tmp_path, ident, committer):
    r = run(tmp_path, [commit("b" * 40, ident, committer)])
    assert r.returncode == 0, r.stdout


def test_fork_author_noreply_passes_only_for_that_author(tmp_path):
    fork = [commit("c" * 40, ("Ann", "555+ann@users.noreply.github.com"))]
    assert run(tmp_path, fork, "--author-login", "ann", "--author-id", "555").returncode == 0
    assert run(tmp_path, fork, "--author-login", "bob", "--author-id", "556").returncode == 1


def test_unknown_co_author_trailer_fails(tmp_path):
    msg = "Merge dev\n\nCo-authored-by: x <x@x>\nCo-authored-by: Claude <noreply@anthropic.com>\n"
    r = run(tmp_path, [commit("d" * 40, OWNER, message=msg)])
    assert r.returncode == 1
    assert "Co-authored-by <x@x>" in r.stdout and "anthropic" not in r.stdout.split("Set ")[0]


def test_lookalike_domain_is_not_anthropic():
    assert ci.problems([commit("e" * 40, ("m", "me@evil-anthropic.com.example"))])
    assert ci.problems([commit("e" * 40, ("m", "a@b@anthropic.com"))])


def test_paginated_slurp_shape_and_bad_input(tmp_path):
    pages = [[commit("f" * 40, OWNER)], [commit("0" * 40, ("x", "x@x"))]]
    assert run(tmp_path, pages).returncode == 1
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", "utf-8")
    assert subprocess.run([sys.executable, str(SCRIPT), str(bad)], capture_output=True).returncode == 2


@pytest.mark.parametrize("listed,expected,code", [(3, 3, 0), (2, 3, 1), (250, 250, 1), (250, 300, 1), (3, None, 0)])
def test_incomplete_or_capped_commit_list_fails(tmp_path, listed, expected, code):
    commits = [commit(f"{i:040x}", OWNER) for i in range(listed)]
    args = [] if expected is None else ["--expected-count", str(expected)]
    r = run(tmp_path, commits, *args)
    assert r.returncode == code, r.stdout
    if code:
        assert "at most 250" in r.stdout


def _identity_step():
    yaml = pytest.importorskip("yaml")
    steps = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text("utf-8"))["jobs"]["test"]["steps"]
    return next(s for s in steps if str(s.get("name", "")).startswith("Commit identity"))


def test_step_runs_only_for_prs_into_dev_and_passes_the_commit_count():
    """Release PR dev -> main (author github-actions[bot]) carries old history and must not go red on `test`."""
    s = _identity_step()
    cond = s["if"]
    assert "github.base_ref == 'dev'" in cond and "github.event_name == 'pull_request'" in cond
    assert s["env"]["PR_COMMITS"] == "${{ github.event.pull_request.commits }}"
    assert '--expected-count "$PR_COMMITS"' in s["run"]


def linked(c, login, role="author"):
    """Attach the GitHub account the API resolved for this commit's author / committer email."""
    c[role] = {"login": login, "id": 777}
    return c


def test_pr_author_own_email_passes_1443(tmp_path):
    """#1443: a contributor commits with their own identity; GitHub links that email to the PR author's account."""
    mine = [linked(commit("e" * 40, ("Jerry", "jerry@example.com")), "Jerry-Tseng", "author")]
    mine[0]["committer"] = {"login": "Jerry-Tseng", "id": 777}
    assert run(tmp_path, mine, "--author-login", "jerry-tseng", "--author-id", "777").returncode == 0
    # the same email is not allowed for another PR author, nor when GitHub did not link it to the PR author
    assert run(tmp_path, mine, "--author-login", "bob", "--author-id", "556").returncode == 1
    unlinked = [commit("f" * 40, ("Jerry", "jerry@example.com"))]
    assert run(tmp_path, unlinked, "--author-login", "jerry-tseng", "--author-id", "777").returncode == 1


def test_pr_author_email_allowed_as_co_author_but_strangers_still_fail_1443(tmp_path):
    msg = "fix\n\nCo-authored-by: Jerry <jerry@example.com>\nCo-authored-by: x <x@x>\n"
    c = linked(commit("1" * 40, ("Jerry", "jerry@example.com"), message=msg), "Jerry-Tseng")
    c["committer"] = {"login": "Jerry-Tseng", "id": 777}
    r = run(tmp_path, [c], "--author-login", "Jerry-Tseng", "--author-id", "777")
    assert r.returncode == 1
    assert "<x@x>" in r.stdout and "jerry@example.com" not in r.stdout
