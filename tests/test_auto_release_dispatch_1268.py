"""#1268: auto-release.yml can be dispatched (on dev) and then evaluates the dev tip like the schedule."""
import os
import pathlib
import subprocess

import yaml

WF = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows" / "auto-release.yml"


def _doc():
    return yaml.safe_load(WF.read_text(encoding="utf-8"))


def test_workflow_dispatch_trigger_present():
    on = _doc().get(True, _doc().get("on"))  # PyYAML parses `on` as True
    assert "workflow_dispatch" in on and "schedule" in on


def test_job_condition_allows_dispatch_event():
    cond = _doc()["jobs"]["auto-release"]["if"]
    # evaluate the condition's event_name part for a dispatch with AUTO_RELEASE=true
    cond = " ".join(cond.split())
    assert "github.event_name == 'workflow_dispatch'" in cond.split("||")[0] + "||" + cond.split("||")[1]


def test_sha_step_uses_dev_tip_on_dispatch(tmp_path):
    step = next(s for s in _doc()["jobs"]["auto-release"]["steps"] if s.get("id") == "sha")
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\necho devsha123\n")
    gh.chmod(0o755)
    out = tmp_path / "out"
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "GITHUB_EVENT_NAME": "workflow_dispatch",
           "GITHUB_REPOSITORY": "o/r", "GITHUB_OUTPUT": str(out), "NAME": "", "RUN_SHA": ""}
    subprocess.run(["bash", "-c", step["run"]], env=env, check=True)
    assert "sha=devsha123" in out.read_text()
