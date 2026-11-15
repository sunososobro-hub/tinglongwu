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
