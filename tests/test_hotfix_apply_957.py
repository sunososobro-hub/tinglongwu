"""#957 / #950 part 2: `hotfix_plan.py apply` puts exactly the fix on the base tag and sets __version__ (real git)."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "hotfix_plan.py"
spec = importlib.util.spec_from_file_location("hotfix_plan_apply", SCRIPT)
hp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hp)

ID = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]


def git(repo, *args):
    return subprocess.run(["git", *ID, *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def write(repo, rel, text):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    """base tag v0.3.5 -> dev: an unrelated feature, then the fix (each on its own file)."""
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q", "-b", "dev")
    write(r, "tinglongwu/__init__.py", '"""x"""\n__version__ = "0.3.5"\n')
    write(r, "app.py", "bug = True\n")
    write(r, "packaging/runtime_layer.py", "KEY = 1\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "base")
    git(r, "tag", "v0.3.5")
    write(r, "feature.py", "new = 1\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "feature not for the hotfix")
    write(r, "app.py", "bug = False\n")
    git(r, "commit", "-q", "-am", "fix #77")
    fix = git(r, "rev-parse", "HEAD")
    git(r, "checkout", "-q", "--detach", "v0.3.5")
    return r, fix


def test_apply_ships_only_the_fix_with_the_new_version(repo):
    r, fix = repo
    res = hp.apply(fix, "v0.3.6", cwd=str(r))
    assert (r / "app.py").read_text() == "bug = False\n"
    assert not (r / "feature.py").exists()  # dev's other changes stay out
    assert '__version__ = "0.3.6"' in (r / "tinglongwu/__init__.py").read_text()
    assert git(r, "rev-list", "--count", "v0.3.5..HEAD") == "2"
    assert res == {"head": git(r, "rev-parse", "HEAD"), "tree": git(r, "rev-parse", "HEAD^{tree}")}
    assert f"cherry picked from commit {fix}" in git(r, "log", "-1", "--format=%B", "HEAD~1")


def test_apply_handles_a_merge_commit_fix(repo):
    r, _ = repo
    git(r, "checkout", "-q", "-b", "side", "v0.3.5")
    write(r, "other.py", "fixed = True\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "fix on a branch")
    git(r, "checkout", "-q", "dev")
    git(r, "merge", "-q", "--no-ff", "-m", "Merge pull request #9", "side")
    merge = git(r, "rev-parse", "HEAD")
    git(r, "checkout", "-q", "--detach", "v0.3.5")
    hp.apply(merge, "v0.3.6", cwd=str(r))
    assert (r / "other.py").read_text() == "fixed = True\n"
    assert not (r / "feature.py").exists() and (r / "app.py").read_text() == "bug = True\n"


def test_apply_refuses_a_conflicting_fix_and_leaves_the_base(repo):
    r, _ = repo
    git(r, "checkout", "-q", "dev")
    write(r, "feature.py", "new = 2\n")  # needs the feature commit that is not in the base
    git(r, "commit", "-q", "-am", "fix on top of the feature")
    fix = git(r, "rev-parse", "HEAD")
    git(r, "checkout", "-q", "--detach", "v0.3.5")
    with pytest.raises(hp.PlanError, match="conflicts in: feature.py"):
        hp.apply(fix, "v0.3.6", cwd=str(r))
    assert git(r, "rev-parse", "HEAD") == git(r, "rev-parse", "v0.3.5")
    assert git(r, "status", "--porcelain") == ""


def test_apply_refuses_a_fix_already_in_the_base(repo):
    r, _ = repo
    with pytest.raises(hp.PlanError, match="changes nothing"):
        hp.apply("v0.3.5", "v0.3.6", cwd=str(r))
    assert git(r, "rev-parse", "HEAD") == git(r, "rev-parse", "v0.3.5")


def test_apply_refuses_a_base_without_the_layered_core(repo):
    r, fix = repo
    git(r, "rm", "-q", "packaging/runtime_layer.py")
    git(r, "commit", "-q", "-m", "like v0.3.5: no runtime layer")
    before = git(r, "rev-parse", "HEAD")
    with pytest.raises(hp.PlanError, match="predates the layered core"):
        hp.apply(fix, "v0.3.6", cwd=str(r))
    assert git(r, "rev-parse", "HEAD") == before


def test_apply_keeps_uncommitted_tracked_edits(repo):
    r, fix = repo
    write(r, "app.py", "local edit\n")
    with pytest.raises(hp.PlanError, match="uncommitted changes"):
        hp.apply(fix, "v0.3.6", cwd=str(r))
    assert (r / "app.py").read_text() == "local edit\n"


def test_apply_rejects_bad_tag(repo):
    r, fix = repo
    with pytest.raises(hp.PlanError, match="not vX.Y.Z"):
        hp.apply(fix, "0.3.6", cwd=str(r))


def test_cli_apply_writes_github_output(repo, tmp_path):
    r, fix = repo
    out = tmp_path / "out.txt"
    env = dict(os.environ, GITHUB_OUTPUT=str(out))
    p = subprocess.run([sys.executable, str(SCRIPT), "apply", "--fix", fix, "--tag", "v0.3.6"],
                       cwd=r, capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    lines = dict(x.split("=", 1) for x in out.read_text().split())
    assert lines["head"] == git(r, "rev-parse", "HEAD") and lines["tree"] == git(r, "rev-parse", "HEAD^{tree}")
    bad = subprocess.run([sys.executable, str(SCRIPT), "apply", "--fix", "v0.3.5", "--tag", "v0.3.6"],
                         cwd=r, capture_output=True, text=True)
    assert bad.returncode == 1 and "hotfix_plan:" in bad.stderr


GATES = [("plan", "success"), ("test", "success"), ("smoke", "success"), ("upgrade overlay", "success")]


def test_summary_lists_every_gate_and_the_wall_time():
    md, ok = hp.summary(GATES, "2026-11-15T03:00:00Z", "2026-11-15T03:17:30Z")
    assert ok
    for name, _ in GATES:
        assert f"| {name} | pass (success) |" in md
    assert "dispatch -> artifacts uploaded): 17.5 min (budget 20 min)" in md and "OVER" not in md


def test_summary_flags_a_failed_gate_and_over_budget():
    md, ok = hp.summary(GATES[:2] + [("smoke", "failure"), ("upgrade overlay", "")], "2026-11-15T03:00:00Z")
    assert not ok
    assert "| smoke | FAIL (failure) |" in md and "| upgrade overlay | FAIL (not run) |" in md
    assert "no artifact uploaded" in md
    md, ok = hp.summary(GATES, "2026-11-15T03:00:00Z", "2026-11-15T03:25:00Z")
    assert ok and "25.0 min (budget 20 min) - OVER BUDGET" in md
    assert hp.summary([], "2026-11-15T03:00:00Z", "2026-11-15T03:01:00Z")[1] is False


def test_cli_summary_exit_code_follows_the_gates(tmp_path):
    out = tmp_path / "summary.md"
    env = dict(os.environ, GITHUB_STEP_SUMMARY=str(out))
    args = [sys.executable, str(SCRIPT), "summary", "--gate", "plan=success", "--gate", "smoke=failure",
            "--started", "2026-11-15T03:00:00Z"]
    assert subprocess.run(args, capture_output=True, text=True, env=env).returncode == 1
    assert "| smoke | FAIL (failure) |" in out.read_text()
    ok = [sys.executable, str(SCRIPT), "summary", "--gate", "plan=success", "--started", "2026-11-15T03:00:00Z",
          "--uploaded", "2026-11-15T03:10:00Z"]
    assert subprocess.run(ok, capture_output=True, text=True).returncode == 0


def test_cli_summary_gate_name_may_contain_an_equals_sign(tmp_path):
    # hotfix.yml passes "tested files = recorded sha256=<outcome>"; the result is after the LAST '='
    # (run 43721057768 failed every gate check because the name was split at the first '=').
    out = tmp_path / "summary.md"
    env = dict(os.environ, GITHUB_STEP_SUMMARY=str(out))
    args = [sys.executable, str(SCRIPT), "summary", "--gate", "plan=success",
            "--gate", "tested files = recorded sha256=success",
            "--started", "2026-11-15T13:50:03Z", "--uploaded", "2026-11-15T13:58:57Z"]
    assert subprocess.run(args, capture_output=True, text=True, env=env).returncode == 0
    assert "| tested files = recorded sha256 | pass (success) |" in out.read_text()
    bad = args[:6] + ["tested files = recorded sha256=failure"] + args[7:]
    assert subprocess.run(bad, capture_output=True, text=True).returncode == 1
