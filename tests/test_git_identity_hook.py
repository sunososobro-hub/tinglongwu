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


def test_cloud_session_sets_identity_in_repo_only(setup):
    home, repo = setup
    env = _env(home, remote=True)
    assert _run_hook(env, repo).returncode == 0
    assert _git(env, "config", "--local", "user.name", cwd=repo) == OWNER_NAME
    assert _git(env, "config", "--local", "user.email", cwd=repo) == OWNER_EMAIL
    assert _git(env, "config", "--global", "--get", "user.email") == ""
    assert _git(env, "config", "--global", "--get", "core.hooksPath") == ""


def test_local_session_undoes_old_hook_damage(setup):
    home, repo = setup
    env = _env(home, remote=False)
    old = home / ".tlwu-git-hooks"
    old.mkdir()
    (old / "commit-msg").write_text("#!/bin/sh\n")
    _git(env, "config", "--global", "user.name", OWNER_NAME)
    _git(env, "config", "--global", "user.email", OWNER_EMAIL)
    _git(env, "config", "--global", "core.hooksPath", str(old))
    r = _run_hook(env, repo)
    assert r.returncode == 0
    assert "#1309" in r.stdout
    assert _git(env, "config", "--global", "--get", "core.hooksPath") == ""
    assert _git(env, "config", "--global", "--get", "user.email") == ""
    assert _git(env, "config", "--global", "--get", "user.name") == ""
    assert not old.exists()


def test_local_cleanup_keeps_contributor_identity_when_only_hookspath_was_set(setup):
    home, repo = setup
    env = _env(home, remote=False)
    old = home / ".tlwu-git-hooks"
    old.mkdir()
    _git(env, "config", "--global", "user.name", "Contributor")
    _git(env, "config", "--global", "user.email", "c@example.com")
    _git(env, "config", "--global", "core.hooksPath", str(old))
    assert _run_hook(env, repo).returncode == 0
    assert _git(env, "config", "--global", "--get", "core.hooksPath") == ""
    assert _git(env, "config", "--global", "user.email") == "c@example.com"


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
