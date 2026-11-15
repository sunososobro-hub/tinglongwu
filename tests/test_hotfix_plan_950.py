"""#956 / #950 part 1: scripts/hotfix_plan.py decides tag, base and fix commit for a hotfix (GitHub API stubbed)."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "hotfix_plan.py"
spec = importlib.util.spec_from_file_location("hotfix_plan", SCRIPT)
hp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hp)

SHA = "c" * 40


def make_api(*, pr_merged=True, base="dev", labels=("bug", "P1"), compare="behind", tags=("v0.3.5",),
             latest="v0.3.5", body="Fix it.\n\nCloses #77", merged_prs=True):
    def api(path):
        if path.startswith("pulls/"):
            return {"merged": pr_merged, "base": {"ref": base}, "merge_commit_sha": SHA, "body": body}
        if path.startswith("compare/"):
            return {"status": compare}
        if path.endswith("/pulls"):
            return [{"merged_at": "2026-11-15T00:00:00Z", "body": body}] if merged_prs else []
        if path.startswith("commits/"):
            return {"sha": SHA}
        if path.startswith("issues/"):
            return {"labels": [{"name": n} for n in labels]}
        if path == "releases/latest":
            return {"tag_name": latest}
        if path.startswith("git/ref/tags/"):
            if path.rsplit("/", 1)[1] in tags:
                return {"ref": path}
            raise hp.NotFound(path)
        raise AssertionError(path)
    return api


def test_accepts_merged_pr():
    assert hp.plan("123", make_api()) == {"tag": "v0.3.6", "base": "v0.3.5", "fix": SHA, "issue": 77}


def test_accepts_sha_on_dev():
    assert hp.plan(SHA[:10], make_api(labels=("bug", "P0")))["fix"] == SHA


def test_rejects_sha_not_on_dev():
    with pytest.raises(hp.PlanError, match="ancestor"):
        hp.plan(SHA, make_api(compare="ahead"))


def test_rejects_unmerged_pr_and_wrong_base():
    with pytest.raises(hp.PlanError, match="not merged"):
        hp.plan("5", make_api(pr_merged=False))
    with pytest.raises(hp.PlanError, match="not merged into dev"):
        hp.plan("5", make_api(base="main"))


def test_rejects_p2_issue():
    with pytest.raises(hp.PlanError, match="P0/P1"):
        hp.plan("5", make_api(labels=("bug", "P2")))


def test_rejects_non_bug_issue():
    with pytest.raises(hp.PlanError, match="not labelled bug"):
        hp.plan("5", make_api(labels=("enhancement", "P1")))


def test_rejects_existing_tag():
    with pytest.raises(hp.PlanError, match="already exists"):
        hp.plan("5", make_api(tags=("v0.3.5", "v0.3.6")))


def test_rejects_missing_issue_link_and_garbage_fix():
    with pytest.raises(hp.PlanError, match="Closes/Refs"):
        hp.plan("5", make_api(body="no link"))
    with pytest.raises(hp.PlanError, match="neither"):
        hp.plan("not-a-sha!", make_api())


def test_next_tag_bumps_patch_only():
    assert hp.next_tag("v1.2.9") == "v1.2.10"
    with pytest.raises(hp.PlanError):
        hp.next_tag("v1.2")


def test_cli_prints_output_lines_and_exit_code(tmp_path):
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\necho 'gh: HTTP 404 Not Found' >&2\nexit 1\n")
    gh.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    r = subprocess.run([sys.executable, str(SCRIPT), "plan", "--fix", "9", "--repo", "o/r"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 1 and "hotfix_plan:" in r.stderr and not r.stdout
