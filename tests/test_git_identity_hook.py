"""#1309: the SessionStart hook must never write a contributor's global git config."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "git_identity.sh"
OWNER_NAME = "sunososobro-hub"
OWNER_EMAIL = "243713070+sunososobro-hub@users.noreply.github.com"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None,
                                reason="needs bash and git")


def _env(home: Path, remote: bool) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "CLAUDE_CODE_REMOTE"}
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")  # never the caller's real XDG git config (#1445)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    if remote:
        env["CLAUDE_CODE_REMOTE"] = "true"
    return env


def _git(env: dict, *args: str, cwd: Path | None = None) -> str:
    r = subprocess.run(["git", *args], env=env, cwd=cwd, capture_output=True, text=True)
    return r.stdout.strip()


def _run_hook(env: dict, repo: Path) -> subprocess.CompletedProcess:
    env = dict(env, CLAUDE_PROJECT_DIR=str(repo))
    return subprocess.run(["bash", str(HOOK)], env=env, cwd=repo, capture_output=True, text=True)


@pytest.fixture
def setup(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], env=_env(home, False), check=True)
    return home, repo


def test_local_session_leaves_contributor_global_config_alone(setup):
    home, repo = setup
    env = _env(home, remote=False)
    _git(env, "config", "--global", "user.name", "Contributor")
    _git(env, "config", "--global", "user.email", "c@example.com")
    assert _run_hook(env, repo).returncode == 0
    assert _git(env, "config", "--global", "user.name") == "Contributor"
    assert _git(env, "config", "--global", "user.email") == "c@example.com"
    assert _git(env, "config", "--global", "--get", "core.hooksPath") == ""
    assert _git(env, "config", "--local", "--get", "user.email", cwd=repo) == ""
    assert not (home / ".tlwu-git-hooks").exists()


UPSTREAM = "https://github.com/sunososobro-hub/tinglongwu.git"
FORK = "https://github.com/someone/tinglongwu.git"


def _snapshot(home: Path) -> dict:
    return {str(p.relative_to(home)): (p.read_bytes() if p.is_file() else None)
            for p in sorted(home.rglob("*"))}


@pytest.mark.parametrize("origin", [UPSTREAM, "git@github.com:sunososobro-hub/tinglongwu.git",
                                    "https://github.com/sunososobro-hub/tinglongwu/"])
def test_cloud_session_sets_identity_in_upstream_repo_only(setup, origin):
    home, repo = setup
    env = _env(home, remote=True)
    _git(env, "remote", "add", "origin", origin, cwd=repo)
    assert _run_hook(env, repo).returncode == 0
    assert _git(env, "config", "--local", "user.name", cwd=repo) == OWNER_NAME
    assert _git(env, "config", "--local", "user.email", cwd=repo) == OWNER_EMAIL
    assert _git(env, "config", "--global", "--get", "user.email") == ""
    assert _git(env, "config", "--global", "--get", "core.hooksPath") == ""


@pytest.mark.parametrize("origin", [FORK, "https://github.com/x/sunososobro-hub-tinglongwu.git", None])
def test_cloud_session_on_a_fork_or_without_origin_writes_nothing(setup, origin):
    home, repo = setup
    env = _env(home, remote=True)
    if origin:
        _git(env, "remote", "add", "origin", origin, cwd=repo)
    before = _snapshot(home)
    assert _run_hook(env, repo).returncode == 0
    assert _git(env, "config", "--local", "--get", "user.email", cwd=repo) == ""
    assert _git(env, "config", "--local", "--get", "user.name", cwd=repo) == ""
    assert _snapshot(home) == before


def _leftover(home: Path, env: dict, hooks_value: str | None, owner_identity: bool) -> None:
    old = home / ".tlwu-git-hooks"
    old.mkdir()
    (old / "commit-msg").write_text("#!/bin/sh\n")
    if owner_identity:
        _git(env, "config", "--global", "user.name", OWNER_NAME)
        _git(env, "config", "--global", "user.email", OWNER_EMAIL)
    else:
        _git(env, "config", "--global", "user.name", "Contributor")
        _git(env, "config", "--global", "user.email", "c@example.com")
    if hooks_value is not None:
        _git(env, "config", "--global", "core.hooksPath", hooks_value)


@pytest.mark.parametrize("hooks_value", ["{home}/.tlwu-git-hooks", "{home}/.tlwu-git-hooks/",
                                         "C:/Users/x/.tlwu-git-hooks", "C:\\Users\\x\\.tlwu-git-hooks"])
def test_local_session_detects_old_hookspath_and_changes_nothing(setup, hooks_value):
    home, repo = setup
    env = _env(home, remote=False)
    _leftover(home, env, hooks_value.format(home=home), owner_identity=False)
    before = _snapshot(home)
    r = _run_hook(env, repo)
    assert r.returncode == 0
    assert "#1309" in r.stdout
    assert "git config --global --unset core.hooksPath" in r.stdout
    assert "rm -rf ~/.tlwu-git-hooks" in r.stdout
    assert _snapshot(home) == before
    assert _git(env, "config", "--local", "--get", "user.email", cwd=repo) == ""


def test_local_session_detects_identity_only_leftover(setup):
    home, repo = setup
    env = _env(home, remote=False)
    _leftover(home, env, None, owner_identity=True)
    before = _snapshot(home)
    r = _run_hook(env, repo)
    assert r.returncode == 0
    assert "#1309" in r.stdout and "user.email" in r.stdout
    assert "--unset core.hooksPath" not in r.stdout
    assert _snapshot(home) == before


def test_local_session_with_unrelated_hookspath_is_silent(setup):
    home, repo = setup
    env = _env(home, remote=False)
    _git(env, "config", "--global", "core.hooksPath", str(home / "my-hooks"))
    _git(env, "config", "--global", "user.email", "c@example.com")
    before = _snapshot(home)
    r = _run_hook(env, repo)
    assert r.returncode == 0 and r.stdout == ""
    assert _snapshot(home) == before


def test_env_never_touches_callers_xdg_git_config(setup, tmp_path, monkeypatch):
    """#1445: a caller's XDG_CONFIG_HOME/git/config must stay byte-identical."""
    home, repo = setup
    xdg = tmp_path / "real_xdg"
    (xdg / "git").mkdir(parents=True)
    cfg = xdg / "git" / "config"
    cfg.write_text("[user]\n\tname = keepme\n")
    before = cfg.read_bytes()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    env = _env(home, remote=True)
    _git(env, "config", "--global", "user.name", "Changed")
    assert _run_hook(env, repo).returncode == 0
    assert _git(env, "config", "--global", "user.name") == "Changed"
    assert cfg.read_bytes() == before
