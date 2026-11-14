"""#570: eval.yml runs the CPU mini-eval + gate only for PRs that touch translation / ASR / eval paths.

The path filter is the workflow's own bash step, executed here against a throwaway git repo (no grep of the YAML
text for the behaviour): a docs-only change must skip, a change under tinglongwu/ must run."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github/workflows/eval.yml"
GIT = shutil.which("git")
BASH = shutil.which("bash")


def _wf():
    return yaml.safe_load(WF.read_text(encoding="utf-8"))


def _job():
    return _wf()["jobs"]["mini-eval"]


def _step(step_id):
    return next(s for s in _job()["steps"] if s.get("id") == step_id)


def _paths_decision(tmp_path, changed: list[str], event="pull_request") -> tuple[str, str]:
    """Run the workflow's path-filter step on a repo whose HEAD commit changes `changed`; -> (run, summary)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "HOME": str(tmp_path), "PATH": __import__("os").environ["PATH"]}
    g = lambda *a: subprocess.run([GIT, *a], cwd=repo, env=env, check=True, capture_output=True)  # noqa: E731
    g("init", "-q")
    (repo / "base.txt").write_text("base")
    g("add", "-A")
    g("commit", "-qm", "base")
    for f in changed:
        p = repo / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    g("add", "-A")
    g("commit", "-qm", "pr")
    script = _step("paths")["run"].replace("${{ github.event_name }}", event)
    out, summary = tmp_path / "out", tmp_path / "summary"
    subprocess.run([BASH, "-e", "-c", script], cwd=repo, check=True, capture_output=True,
                   env={**env, "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary)})
    run = re.search(r"^run=(\w+)$", out.read_text(), re.M).group(1)
    return run, summary.read_text() if summary.exists() else ""


needs_tools = pytest.mark.skipif(GIT is None or BASH is None, reason="git and bash are needed to run the step")


@needs_tools
@pytest.mark.parametrize("changed", [["README.md"], ["docs/testing.md", "CONTRIBUTING.md"], ["mobile/x.kt"],
                                     [".github/workflows/ci.yml"], ["tinglongwu_setup/cli.py"]])
def test_docs_and_unrelated_prs_skip_the_eval(tmp_path, changed):
    run, summary = _paths_decision(tmp_path, changed)
    assert run == "false"
    assert "skipped (PASS)" in summary


@needs_tools
@pytest.mark.parametrize("changed", [["tinglongwu/translate/hymt.py"], ["tinglongwu/data/glossary.json"],
                                     ["tlwu_eval/gate.py"], ["eval/baseline.json"], ["tinglongwu_setup/manifest.json"],
                                     [".github/workflows/eval.yml"], ["README.md", "tinglongwu/merge.py"]])
def test_translation_asr_eval_prs_run_the_eval(tmp_path, changed):
    assert _paths_decision(tmp_path, changed)[0] == "true"


@needs_tools
def test_manual_dispatch_always_runs(tmp_path):
    assert _paths_decision(tmp_path, ["README.md"], event="workflow_dispatch")[0] == "true"


def test_every_heavy_step_is_skipped_for_unrelated_prs():
    steps = _job()["steps"]
    later = steps[[s.get("id") for s in steps].index("paths") + 1:]
    assert later and all("steps.paths.outputs.run == 'true'" in s.get("if", "") for s in later)


def test_runs_the_issue_command_and_gates_on_the_committed_baseline():
    runs = "\n".join(s.get("run", "") for s in _job()["steps"])
    assert "python -m tlwu_eval --smoke --data eval_data/smoke --clips 5" in runs
    assert "--engine hymt2-cpu" in runs and "--device cpu" in runs and "--no-noise" in runs
    assert "python -m tlwu_eval --gate r.json --baseline eval/baseline.json" in runs
    assert "GITHUB_STEP_SUMMARY" in _step_named("Gate")["run"]
    assert _wf()["env"]["WHISPER"] == "small" and _job()["runs-on"].startswith("ubuntu")


def _step_named(prefix):
    return next(s for s in _job()["steps"] if s.get("name", "").startswith(prefix))


def test_security_read_only_no_pr_target_pinned_actions():
    wf = _wf()
    on = wf.get("on", wf.get(True))
    assert "pull_request_target" not in on and "pull_request" in on
    assert wf["permissions"] == {"contents": "read"}
    uses = [s["uses"] for s in _job()["steps"] if "uses" in s]
    assert uses and all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in uses), uses
