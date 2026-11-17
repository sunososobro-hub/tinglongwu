"""#1443: no hook in .claude/settings.json may change anything in a contributor's $HOME.

Every hook command is run the way Claude Code runs it on a contributor's own
machine (CLAUDE_CODE_REMOTE unset) with a temp HOME that already holds the
contributor's own git identity. Afterwards every file under that HOME must be
byte-identical and no file may have been created or removed.
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

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None,
                                reason="needs bash and git")


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


def _sandbox(tmp_path: Path) -> tuple[Path, Path, dict]:
    """A contributor HOME with their own identity, and a clone-like project dir."""
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
    subprocess.run(["git", "init", "-q", str(project)], env=env, check=True)
    return home, project, env


def run_hooks_and_diff_home(tmp_path: Path, settings: Path | None = None) -> dict[str, tuple]:
    """Run every hook command once; return {path: (before, after)} for each change under HOME."""
    home, project, env = _sandbox(tmp_path)
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
