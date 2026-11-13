"""#112: the release workflow's branch check must accept a pre-release tag (vX.Y.Z-suffix) cut from dev,
while a plain vX.Y.Z tag stays main-only.

Runs the workflow's "Tag format and branch check" step in bash inside a throwaway repo whose `origin`
has a `main` and a `dev` branch (dev one commit ahead of main)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(not (BASH and GIT), reason="needs bash and git (the release workflow runs in bash)")


def _step_script() -> str:
    """The `run: |` block of the branch-check step, dedented."""
    lines = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip().startswith("- name: Tag format and branch check"))
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    body = []
    indent = None
    for ln in lines[run + 1:]:
        if ln.strip() and indent is None:
            indent = len(ln) - len(ln.lstrip())
        if ln.strip() and len(ln) - len(ln.lstrip()) < indent:
            break
        body.append(ln[indent:] if ln.strip() else "")
    assert body, "branch-check step has no run script"
    return "\n".join(body) + "\n"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run([GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    base = tmp_path_factory.mktemp("rel")
    origin, work = base / "origin.git", base / "work"
    _git(base, "init", "-q", "--bare", str(origin))
    _git(base, "init", "-q", str(work))
    for k, v in (("user.name", "t"), ("user.email", "t@example.invalid"), ("commit.gpgsign", "false")):
        _git(work, "config", k, v)
    _git(work, "checkout", "-q", "-b", "main")
    _git(work, "commit", "-q", "--allow-empty", "-m", "on main")
    main_sha = _git(work, "rev-parse", "HEAD")
    _git(work, "checkout", "-q", "-b", "dev")
    _git(work, "commit", "-q", "--allow-empty", "-m", "on dev only")
    dev_sha = _git(work, "rev-parse", "HEAD")
    _git(work, "checkout", "-q", "-b", "feature")
    _git(work, "commit", "-q", "--allow-empty", "-m", "on neither")
    other_sha = _git(work, "rev-parse", "HEAD")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-q", "origin", "main", "dev")
    return work, {"main": main_sha, "dev": dev_sha, "other": other_sha}


def _check(repo, tag: str, where: str) -> subprocess.CompletedProcess:
    work, shas = repo
    env = dict(os.environ, TAG=tag, GITHUB_SHA=shas[where])
    return subprocess.run([BASH, "-e", "-c", _step_script()], cwd=work, env=env, capture_output=True, text=True)


@pytest.mark.parametrize("tag", ["v0.3.0", "v0.3.0-beta.1"])
def test_any_tag_on_main_passes(repo, tag):
    r = _check(repo, tag, "main")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("tag", ["v0.3.0-beta.1", "v1.0.0-rc.1"])
def test_prerelease_tag_on_dev_passes(repo, tag):
    r = _check(repo, tag, "dev")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "is on dev" in r.stdout


def test_plain_tag_on_dev_is_rejected(repo):
    r = _check(repo, "v0.3.0", "dev")
    assert r.returncode != 0
    assert "is not on main" in r.stdout


@pytest.mark.parametrize("tag", ["v0.3.0", "v0.3.0-beta.1"])
def test_tag_on_neither_branch_is_rejected(repo, tag):
    assert _check(repo, tag, "other").returncode != 0


def test_bad_tag_is_rejected(repo):
    r = _check(repo, "release-1", "main")
    assert r.returncode != 0
    assert "bad tag" in r.stdout
