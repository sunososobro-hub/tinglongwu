"""#681: hw-tests runs nightly on dev, only when dev changed since the last green run. The gate (scripts/hw_nightly.py)
runs in a GitHub-hosted workflow that dispatches hw-tests.yml, so hw-tests.yml keeps its two triggers and its single
self-hosted job (scripts/hw_deferral.py #848 reads that job's runner to tell "not started" from "started")."""
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "hw_nightly.py"
NIGHTLY = ROOT / ".github" / "workflows" / "hw-nightly.yml"
HW = ROOT / ".github" / "workflows" / "hw-tests.yml"
BASH = shutil.which("bash")
yaml = pytest.importorskip("yaml")

DEV = "d" * 40
OLD = "a" * 40


def _mod():
    assert SCRIPT.is_file(), "scripts/hw_nightly.py is missing (#681)"
    spec = importlib.util.spec_from_file_location("hw_nightly", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run(sha, status="completed", conclusion="success", rid=1):
    return {"id": rid, "head_sha": sha, "status": status, "conclusion": conclusion}


def test_runs_when_dev_has_no_run_yet():
    ok, why = _mod().decide(DEV, [run(OLD)])
    assert ok and DEV[:7] in why


def test_skips_when_dev_is_unchanged_since_the_last_green_run():
    ok, why = _mod().decide(DEV, [run(DEV, rid=7), run(OLD, rid=3)])
    assert not ok and "unchanged" in why and "#7" in why


def test_a_failed_run_on_the_same_commit_is_retried():
    assert _mod().decide(DEV, [run(DEV, conclusion="failure")])[0] is True
    assert _mod().decide(DEV, [run(DEV, conclusion="cancelled")])[0] is True


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "pending"])
def test_skips_while_a_run_for_that_commit_is_queued_or_running(status):
    ok, why = _mod().decide(DEV, [run(DEV, status=status, conclusion=None)])
    assert not ok and status in why  # an offline PC keeps one pending run; the nightly never stacks another


def test_new_dev_commit_runs_even_after_a_green_run_on_an_older_one():
    assert _mod().decide(DEV, [run(OLD, rid=9)])[0] is True
    assert _mod().decide(DEV, [])[0] is True


def _gh_shim(tmp_path, dev_sha, runs):
    shim = tmp_path / "shim"
    shim.mkdir()
    (tmp_path / "runs.json").write_text(json.dumps(runs))
    gh = shim / "gh"
    gh.write_text('#!/bin/bash\n'
                  'echo "gh $*" >> "$GH_LOG"\n'
                  'case "$*" in\n'
                  f'  *"commits/dev"*) echo {dev_sha};;\n'
                  f'  *"workflows/hw-tests.yml/runs"*) cat "{tmp_path / "runs.json"}";;\n'
                  'esac\n')
    gh.chmod(0o755)
    return shim


@pytest.mark.skipif(BASH is None, reason="bash is needed for the gh shim")
@pytest.mark.parametrize("runs,want", [([run(OLD)], "true"), ([run(DEV, rid=5)], "false")])
def test_cli_writes_run_and_sha_outputs_and_a_summary_with_the_commit(tmp_path, runs, want):
    out, summary = tmp_path / "out", tmp_path / "sum"
    shim = _gh_shim(tmp_path, DEV, runs)
    r = subprocess.run(["python3", str(SCRIPT), "--repo", "o/r"], capture_output=True, text=True,
                       env={**os.environ, "PATH": f"{shim}:{os.environ['PATH']}", "GH_LOG": str(tmp_path / "gh.log"),
                            "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary)})
    assert r.returncode == 0, r.stderr
    assert f"run={want}" in out.read_text().splitlines() and f"sha={DEV}" in out.read_text().splitlines()
    assert DEV in summary.read_text()  # the summary names the commit
    calls = (tmp_path / "gh.log").read_text()
    assert "repos/o/r/commits/dev" in calls and "repos/o/r/actions/workflows/hw-tests.yml/runs" in calls


# ---- the workflows

def _wf(path):
    return yaml.safe_load(path.read_text("utf-8"))


def test_nightly_workflow_is_scheduled_hosted_and_never_overlaps():
    assert NIGHTLY.is_file(), ".github/workflows/hw-nightly.yml is missing (#681)"
    wf = _wf(NIGHTLY)
    on = wf[True]  # YAML 1.1 reads the key `on` as True
    assert on["schedule"][0]["cron"] and "workflow_dispatch" in on and "pull_request" not in on
    assert wf["concurrency"]["group"] and wf["concurrency"]["cancel-in-progress"] is False
    job = wf["jobs"]["nightly"]
    assert job["runs-on"] == "ubuntu-24.04"  # never the self-hosted runner: only hw-tests.yml may use that
    assert job["permissions"] == {"contents": "read", "actions": "write"}


def test_nightly_dispatches_hw_tests_on_dev_only_when_the_gate_says_run():
    steps = _wf(NIGHTLY)["jobs"]["nightly"]["steps"]
    gate = next(s for s in steps if s.get("id") == "gate")
    assert "scripts/hw_nightly.py" in gate["run"]
    dispatch = next(s for s in steps if "gh workflow run hw-tests.yml" in s.get("run", ""))
    assert "--ref dev" in dispatch["run"] and dispatch["if"] == "steps.gate.outputs.run == 'true'"


def test_hw_tests_keeps_its_triggers_and_its_single_self_hosted_job():
    wf = _wf(HW)
    assert set(wf[True]) == {"push", "workflow_dispatch"}  # no schedule / pull_request here (security header)
    assert list(wf["jobs"]) == ["hw"] and wf["jobs"]["hw"]["runs-on"] == ["self-hosted", "windows", "tlwu-hw"]
    assert wf["concurrency"] == {"group": "hw-tests", "cancel-in-progress": False}  # runs never overlap


@pytest.mark.skipif(BASH is None, reason="bash is needed to run the step")
def test_hw_tests_summary_shows_the_commit_under_test(tmp_path):
    steps = _wf(HW)["jobs"]["hw"]["steps"]
    step = next(s for s in steps if s.get("name", "").startswith("Commit under test"))
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "HOME": str(tmp_path), "PATH": os.environ["PATH"]}
    g = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, env=env, check=True, capture_output=True, text=True)  # noqa: E731
    g("init", "-q")
    (tmp_path / "f").write_text("x")
    g("add", "-A")
    g("commit", "-qm", "c")
    sha = g("rev-parse", "HEAD").stdout.strip()
    summary = tmp_path / "sum"
    subprocess.run([BASH, "-e", "-o", "pipefail", "-c", step["run"]], cwd=tmp_path, check=True, capture_output=True,
                   env={**env, "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_EVENT_NAME": "workflow_dispatch"})
    assert sha in summary.read_text()
