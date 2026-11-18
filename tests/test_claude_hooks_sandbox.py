"""#1443: no hook in .claude/settings.json may change anything in a contributor's $HOME.

Every hook command is run the way Claude Code runs it on a contributor's own
machine (CLAUDE_CODE_REMOTE unset) with a temp HOME that already holds the
contributor's own git identity. Afterwards every file under that HOME must be
byte-identical and no file may have been created or removed. The same holds for a cloud
session (CLAUDE_CODE_REMOTE=true) in a clone of a fork.

The checked-in agent config is also kept small: `.claude/settings.json` may only hold
allow-listed hook commands (no env, permissions, mcpServers, enabledPlugins, ...), and no
`.mcp.json` is committed. Missing bash or git fails the run instead of skipping it, so the
sandbox can never pass by not running.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / ".claude" / "settings.json"

# Top-level keys .claude/settings.json may have, and the exact hook commands it may run. A new
# hook or key is a reviewed change to this list (.claude/ and this guard are Opus-reviewed paths).
ALLOWED_SETTINGS_KEYS = frozenset({"$schema", "hooks"})
ALLOWED_HOOK_COMMANDS = frozenset({
    ("SessionStart", 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/git_identity.sh"'),
})
FORK_URL = "https://github.com/someone-else/tinglongwu.git"


def _require_tools() -> None:
    # #1443 F9: fail, never skip, when the sandbox cannot run the hooks.
    missing = [t for t in ("bash", "git") if shutil.which(t) is None]
    assert not missing, f"hook sandbox needs {missing} on PATH; install it rather than skipping this test"


def _hook_commands(settings: Path) -> list[tuple[str, str]]:
    """(event, command) for every `type: command` hook in a settings.json."""
    data = json.loads(settings.read_text(encoding="utf-8"))
    out = []
    for event, groups in (data.get("hooks") or {}).items():
        for group in groups or []:
            for hook in group.get("hooks") or []:
                if hook.get("type") == "command" and hook.get("command"):
                    out.append((event, hook["command"]))
    return out


def _snapshot(home: Path) -> dict[str, str]:
    snap = {}
    for p in sorted(home.rglob("*")):
        rel = p.relative_to(home).as_posix()
        if p.is_symlink():
            snap[rel] = "link:" + os.readlink(p)
        elif p.is_dir():
            snap[rel] = "dir"
        else:
            snap[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snap


def _sandbox(tmp_path: Path, remote: bool = False, origin: str | None = None) -> tuple[Path, Path, dict]:
    """A contributor HOME with their own identity, and a clone-like project dir."""
    _require_tools()
    home = tmp_path / "home"
    (home / ".config" / "git").mkdir(parents=True)
    (home / ".gitconfig").write_text(
        "[user]\n\tname = Contributor\n\temail = contributor@example.com\n", encoding="utf-8")
    (home / ".config" / "git" / "config").write_text("[core]\n\tautocrlf = input\n", encoding="utf-8")
    (home / ".bashrc").write_text("# contributor profile\n", encoding="utf-8")

    project = tmp_path / "project"
    project.mkdir()
    shutil.copytree(ROOT / ".claude", project / ".claude")

    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and k not in ("CLAUDE_CODE_REMOTE", "XDG_CONFIG_HOME")}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
               GIT_CONFIG_NOSYSTEM="1", CLAUDE_PROJECT_DIR=str(project))
    if remote:
        env["CLAUDE_CODE_REMOTE"] = "true"
    subprocess.run(["git", "init", "-q", str(project)], env=env, check=True)
    if origin:
        subprocess.run(["git", "-C", str(project), "remote", "add", "origin", origin], env=env, check=True)
    return home, project, env


def run_hooks_and_diff_home(tmp_path: Path, settings: Path | None = None, remote: bool = False,
                            origin: str | None = None) -> dict[str, tuple]:
    """Run every hook command once; return {path: (before, after)} for each change under HOME."""
    home, project, env = _sandbox(tmp_path, remote=remote, origin=origin)
    commands = _hook_commands(settings or project / ".claude" / "settings.json")
    before = _snapshot(home)
    for _event, command in commands:
        subprocess.run(["bash", "-c", command], env=env, cwd=project,
                       capture_output=True, text=True, timeout=60)
    after = _snapshot(home)
    return {k: (before.get(k), after.get(k))
            for k in set(before) | set(after) if before.get(k) != after.get(k)}


def test_settings_has_hook_commands():
    # Guard against the sandbox test silently passing on an unparsed file.
    assert _hook_commands(SETTINGS), "no command hooks found in .claude/settings.json"


def test_hooks_never_change_contributor_home(tmp_path):
    changed = run_hooks_and_diff_home(tmp_path)
    assert changed == {}, f"hooks changed files under the contributor's HOME: {sorted(changed)}"


def test_sandbox_catches_a_hook_that_writes_global_git_config(tmp_path):
    # The pre-#1442 hook did `git config --global ...`; the sandbox must flag that.
    project_settings = tmp_path / "bad_settings.json"
    project_settings.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
        {"type": "command", "command": "git config --global user.name owner"}]}]}}), encoding="utf-8")
    changed = run_hooks_and_diff_home(tmp_path, settings=project_settings)
    assert ".gitconfig" in changed


def test_hooks_never_change_home_in_a_cloud_session_on_a_fork(tmp_path):
    # #1443 F9: CLAUDE_CODE_REMOTE=true in a fork clone must not touch HOME either.
    changed = run_hooks_and_diff_home(tmp_path, remote=True, origin=FORK_URL)
    assert changed == {}, f"hooks changed files under HOME in a fork cloud session: {sorted(changed)}"


def test_sandbox_catches_a_global_write_in_a_cloud_session(tmp_path):
    bad = tmp_path / "bad_remote.json"
    bad.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command":
        '[ "$CLAUDE_CODE_REMOTE" = true ] && git config --global user.email x@x'}]}]}}), encoding="utf-8")
    changed = run_hooks_and_diff_home(tmp_path, settings=bad, remote=True, origin=FORK_URL)
    assert ".gitconfig" in changed


def settings_problems(settings: Path) -> list[str]:
    """Everything in a settings.json outside ALLOWED_SETTINGS_KEYS / ALLOWED_HOOK_COMMANDS."""
    data = json.loads(settings.read_text(encoding="utf-8"))
    out = [f"key {k!r} is not allowed" for k in sorted(set(data) - ALLOWED_SETTINGS_KEYS)]
    for event, groups in (data.get("hooks") or {}).items():
        for group in groups or []:
            for hook in group.get("hooks") or []:
                if (event, hook.get("command")) not in ALLOWED_HOOK_COMMANDS or hook.get("type") != "command":
                    out.append(f"hook {event}: {hook!r} is not allow-listed")
    return out


def test_settings_only_hold_allow_listed_hooks():
    assert settings_problems(SETTINGS) == []


@pytest.mark.parametrize("extra", [
    {"env": {"GIT_AUTHOR_EMAIL": "x@x"}},
    {"permissions": {"allow": ["Bash(*)"]}},
    {"mcpServers": {"x": {"command": "x"}}},
    {"enabledPlugins": {"x@y": True}},
    {"hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}},
])
def test_settings_guard_flags_unlisted_keys_and_hooks(tmp_path, extra):
    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    data.update(extra)
    bad = tmp_path / "settings.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    assert settings_problems(bad)


def _git_env() -> dict:
    # No inherited GIT_* (GIT_DIR, GIT_INDEX_FILE, ...) may point git at another repo or index.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def tracked_agent_config(root: Path) -> list[str]:
    """Committed files that would configure a contributor's Claude Code beyond settings.json hooks."""
    # -z: unquoted, NUL-separated paths (a quoted "dir\t/.mcp.json" must not slip through).
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, text=True,
                         env=_git_env(), check=True).stdout
    files = [f for f in out.split("\0") if f]
    return [f for f in files if Path(f).name == ".mcp.json" or f == ".claude/settings.local.json"]


def test_no_committed_mcp_json():
    _require_tools()
    assert tracked_agent_config(ROOT) == []


@pytest.mark.parametrize("rel", [".mcp.json", "odd\tdir/.mcp.json"])
def test_mcp_json_guard_catches_a_committed_file(tmp_path, rel):
    _require_tools()
    env = _git_env()
    subprocess.run(["git", "init", "-q", str(tmp_path)], env=env, check=True)
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "--", rel], env=env, check=True)
    assert tracked_agent_config(tmp_path) == [rel]


def test_missing_bash_fails_instead_of_skipping(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "bash" else "/usr/bin/" + name)
    with pytest.raises(AssertionError, match="bash"):
        _require_tools()
