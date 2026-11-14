"""#873: e2e.yml skips its heavy steps on docs-only PRs (jobs still report success); stats-dashboard has no push trigger.

The decide step is the workflow's own bash, executed against a throwaway git repo whose `origin/dev` is the base."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
ROOT = Path(__file__).resolve().parent.parent
E2E = ROOT / ".github/workflows/e2e.yml"
STATS = ROOT / ".github/workflows/stats-dashboard.yml"
GIT = shutil.which("git")
BASH = shutil.which("bash")
JOBS = ("linux", "windows", "macos")
needs_tools = pytest.mark.skipif(GIT is None or BASH is None, reason="git and bash are needed to run the step")


def _jobs():
    return yaml.safe_load(E2E.read_text(encoding="utf-8"))["jobs"]


def _decide(tmp_path, changed, event="pull_request", job="linux") -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "HOME": str(tmp_path), "PATH": os.environ["PATH"]}
    g = lambda *a: subprocess.run([GIT, *a], cwd=repo, env=env, check=True, capture_output=True)  # noqa: E731
    g("init", "-q")
    (repo / "base.txt").write_text("base")
    g("add", "-A")
    g("commit", "-qm", "base")
    g("update-ref", "refs/remotes/origin/dev", "HEAD")
    for f in changed:
        p = repo / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    g("add", "-A")
    g("commit", "-qm", "pr")
    step = next(s for s in _jobs()[job]["steps"] if s.get("id") == "changes")
    out = tmp_path / "out"
    subprocess.run([BASH, "-e", "-c", step["run"]], cwd=repo, check=True, capture_output=True,
                   env={**env, "GITHUB_OUTPUT": str(out), "GITHUB_EVENT_NAME": event, "GITHUB_BASE_REF": "dev"})
    return re.search(r"^build=(\w+)$", out.read_text(), re.M).group(1)


@needs_tools
@pytest.mark.parametrize("job", JOBS)
@pytest.mark.parametrize("changed", [["README.md"], ["docs/guide/x.txt"], [".agent/tlwu_rules.md", "docs/a.md"],
                                     ["CONTRIBUTING.md", "README.zh.md"]])
def test_docs_only_pr_skips(tmp_path, job, changed):
    assert _decide(tmp_path, changed, job=job) == "false"


@needs_tools
@pytest.mark.parametrize("changed", [["tinglongwu/core.py"], ["tests/test_x.py"], ["scripts/ci_e2e.py"], ["requirements.txt"],
                                     ["pyproject.toml"], ["packaging/x.spec"], [".github/workflows/e2e.yml"],
                                     ["README.md", "tinglongwu/core.py"], ["tinglongwu/data/glossary.json"]])
def test_code_touching_pr_runs_in_full(tmp_path, changed):
    assert _decide(tmp_path, changed) == "true"


@needs_tools
@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_push_and_dispatch_always_run_in_full(tmp_path, event):
    assert _decide(tmp_path, ["README.md"], event=event) == "true"


@pytest.mark.parametrize("job", JOBS)
def test_every_step_after_the_decision_is_gated_and_nothing_removed(job):
    steps = _jobs()[job]["steps"]
    idx = [s.get("id") for s in steps].index("changes")
    assert idx == 1 and "actions/checkout" in steps[0]["uses"] and steps[0]["with"]["fetch-depth"] == 0
    later = steps[idx + 1:]
    assert later and all("steps.changes.outputs.build == 'true'" in str(s.get("if", "")) for s in later)
    # cancelled() semantics are kept on the steps that had them
    assert all("!cancelled()" in s["if"] for s in later if s.get("name", "").startswith("#"))


def test_no_test_step_was_dropped():
    names = {j: [s.get("name", "") for s in v["steps"]] for j, v in _jobs().items()}
    for j, n in {"linux": 19, "windows": 14, "macos": 11}.items():
        assert len(names[j]) == n
    assert any("#7 firewall_test" in x for x in names["linux"]) and any("#7 firewall_test" in x for x in names["windows"])


def test_e2e_still_triggers_on_push_pr_and_dispatch():
    on = yaml.safe_load(E2E.read_text(encoding="utf-8"))[True]
    assert set(on) == {"push", "pull_request", "workflow_dispatch"}
    assert on["pull_request"]["branches"] == ["dev"] and "paths" not in on["pull_request"]


def test_stats_dashboard_has_no_push_trigger_but_keeps_schedule_and_dispatch():
    on = yaml.safe_load(STATS.read_text(encoding="utf-8"))[True]
    assert "push" not in on and "schedule" in on and "workflow_dispatch" in on
