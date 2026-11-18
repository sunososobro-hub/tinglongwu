"""#1451: the home-write scan flags code that changes a contributor's settings and passes current dev."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import home_write_scan as hws  # noqa: E402

# .claude/hooks/git_identity.sh as of commit 923c2d7 (before #1442): rewrote the global git config.
OLD_HOOK = '''#!/usr/bin/env bash
git config --global user.name "sunososobro-hub" 2>/dev/null
git config --global user.email "243713070+sunososobro-hub@users.noreply.github.com" 2>/dev/null
HOOKS_DIR="${HOME:-/tmp}/.tlwu-git-hooks"
git config --global core.hooksPath "$HOOKS_DIR" 2>/dev/null
'''


def test_flags_pre_1442_hook():
    found = hws.scan_files({".claude/hooks/git_identity.sh": OLD_HOOK})
    assert {f["rule"] for f in found} >= {"git-config-global", "hooks-path"}
    assert len(found) >= 3


def test_flags_shell_profile_and_gitconfig_writes():
    files = {
        "scripts/a.sh": 'echo "export X=1" >> ~/.bashrc\n',
        "scripts/b.sh": 'cp x "$HOME/.gitconfig"\n',
        "scripts/c.sh": 'echo hi | tee -a ~/.zshrc\n',
    }
    found = hws.scan_files(files)
    assert sorted({f["file"] for f in found}) == ["scripts/a.sh", "scripts/b.sh", "scripts/c.sh"]


def test_allows_undo_reads_local_and_comments():
    files = {
        ".claude/hooks/git_identity.sh": 'git config --global --unset core.hooksPath\n'
                                         'git config --global --get user.email\n',
        "scripts/d.sh": 'git config --local user.name x\ngit config --get core.hooksPath\n# git config --global user.name x\n',
    }
    assert hws.scan_files(files) == []


def test_undo_is_only_allowlisted_in_the_cleanup_file():
    assert hws.scan_files({"scripts/x.sh": "git config --global --unset user.name\n"})


def test_current_repo_has_zero_findings():
    assert hws.scan_repo(ROOT) == []


def test_cli_prints_zero_findings():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "home_write_scan.py")], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip().endswith("0 findings")


def _rules(text, path="scripts/z.sh"):
    return {f["rule"] for f in hws.scan_files({path: text})}


def test_git_level_options_before_config():
    assert "git-config-global" in _rules("git -C . config --global user.name x\n")


def test_read_exemption_is_per_command():
    assert "git-config-global" in _rules("git config --global --get user.name || git config --global user.name x\n")
    assert _rules("git config --global --get user.name\n") == set()


def test_cp_flags_destination_only():
    assert _rules('cp "$HOME/.gitconfig" ./backup\n') == set()
    assert "shell-profile-write" in _rules('cp ./backup "$HOME/.gitconfig"\n')


def test_hooks_path_judged_by_value():
    assert "hooks-path" in _rules("git config --local core.hooksPath /tmp/hooks\n")
    assert _rules("git config core.hooksPath .githooks\n") == set()
    assert "hooks-path" in _rules("git -c core.hooksPath=/tmp/h commit\n")


def test_echo_redirect_to_profile_flagged_even_in_allowlisted_file():
    assert "shell-profile-write" in _rules("echo x >> ~/.bashrc\n", ".claude/hooks/git_identity.sh")


def test_json_mode_stdout_is_only_json():
    import json
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "home_write_scan.py"), "--json"], capture_output=True, text=True)
    assert json.loads(r.stdout) == []


def test_ignores_inherited_git_index_file(tmp_path):
    import os
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "x.sh").write_text("git config --global user.name x\n")
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for cmd in (["init", "-q"], ["add", "x.sh"]):
        subprocess.run(["git", "-C", str(repo)] + cmd, check=True, env=clean)
    env = dict(clean, GIT_INDEX_FILE=str(tmp_path / "other-index"))
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "home_write_scan.py"), "--root", str(repo)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 1 and "x.sh:1" in r.stdout


def test_hooks_path_climbing_out_of_repo_flagged():
    assert "hooks-path" in _rules("git config core.hooksPath ../../hooks\n")
    assert _rules("git config core.hooksPath .githooks/sub\n") == set()


def test_echo_exemption_does_not_hide_chained_write():
    assert "git-config-global" in _rules('echo hi && git config --global user.name x\n', ".claude/hooks/git_identity.sh")
    assert _rules('echo "run: a && git config --global user.name x"\n', ".claude/hooks/git_identity.sh") == set()


def test_trailing_comment_after_cp_destination():
    assert "shell-profile-write" in _rules('cp ./b "$HOME/.gitconfig"  # backup\n')


def test_dotdot_prefix_name_is_repo_local():
    assert _rules("git config core.hooksPath ..githooks\n") == set()
    assert "hooks-path" in _rules("git config core.hooksPath ../../hooks\n")
    assert "hooks-path" in _rules("git config core.hooksPath ./../outside-hooks\n")


def test_quoted_hash_is_not_a_comment():
    assert "hooks-path" in _rules('git -C "repo #1" config core.hooksPath /tmp/hooks\n')
    assert _rules('echo "a # b"\n') == set()


def test_escaped_quote_does_not_close_string():
    assert "hooks-path" in _rules("printf '%s\\n' \"a \\\" # b\"; git config core.hooksPath /tmp/hooks\n")
    assert _rules("echo 'it'\"'\"'s # fine'\n") == set()
