"""#976 item 6: a pull_request that only touches mobile/** (plus docs) skips the desktop e2e / audio-routing /
acceptance-windows steps the same way a docs-only PR does (#873); the jobs still report success, and push runs stay full.

The decide step is each workflow's own bash, executed against a throwaway git repo whose `origin/dev` is the base."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github/workflows"
GIT = shutil.which("git")
BASH = shutil.which("bash")
needs_tools = pytest.mark.skipif(GIT is None or BASH is None, reason="git and bash are needed to run the step")

# (workflow file, job) pairs that must skip on a mobile-only PR
JOBS = [("e2e.yml", "linux"), ("e2e.yml", "windows"), ("e2e.yml", "macos"),
        ("audio-routing.yml", "audio-routing"), ("acceptance-windows.yml", "acceptance")]
MOBILE_ONLY = [["mobile/lib/main.dart"], ["mobile/android/app/build.gradle", "mobile/README.md"],
               ["mobile/pubspec.yaml", "docs/mobile.md"]]
DESKTOP = [["tinglongwu/core.py"], ["mobile/lib/main.dart", "tinglongwu/core.py"], ["scripts/x.py"],
           [".github/workflows/mobile-apk.yml"], ["tests/test_x.py"]]


def _steps(wf, job):
    return yaml.safe_load((WF / wf).read_text(encoding="utf-8"))["jobs"][job]["steps"]


def _decide(tmp_path, wf, job, changed, event="pull_request", moves=()) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "HOME": str(tmp_path), "PATH": os.environ["PATH"]}
    g = lambda *a: subprocess.run([GIT, *a], cwd=repo, env=env, check=True, capture_output=True)  # noqa: E731
    g("init", "-q")
    (repo / "base.txt").write_text("base")
    for src, _ in moves:
        (repo / src).parent.mkdir(parents=True, exist_ok=True)
        (repo / src).write_text("unchanged module body\n" * 20)
    g("add", "-A")
    g("commit", "-qm", "base")
    g("update-ref", "refs/remotes/origin/dev", "HEAD")
    for src, dst in moves:
        (repo / dst).parent.mkdir(parents=True, exist_ok=True)
        g("mv", src, dst)
    for f in changed:
        p = repo / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    g("add", "-A")
    g("commit", "-qm", "pr")
    step = next(s for s in _steps(wf, job) if s.get("id") == "changes")
    out = tmp_path / "out"
    subprocess.run([BASH, "-eo", "pipefail", "-c", step["run"]], cwd=repo, check=True, capture_output=True,
                   env={**env, "GITHUB_OUTPUT": str(out), "GITHUB_EVENT_NAME": event, "GITHUB_BASE_REF": "dev"})
    return re.search(r"^build=(\w+)$", out.read_text(), re.M).group(1)


@needs_tools
@pytest.mark.parametrize("wf,job", JOBS)
@pytest.mark.parametrize("changed", MOBILE_ONLY)
def test_mobile_only_pr_skips_desktop_steps(tmp_path, wf, job, changed):
    assert _decide(tmp_path, wf, job, changed) == "false"


@needs_tools
@pytest.mark.parametrize("wf,job", JOBS)
def test_docs_only_pr_skips_too(tmp_path, wf, job):
    assert _decide(tmp_path, wf, job, ["README.md", "docs/a.md"]) == "false"


@needs_tools
@pytest.mark.parametrize("wf,job", JOBS)
@pytest.mark.parametrize("changed", DESKTOP)
def test_desktop_touching_pr_runs_in_full(tmp_path, wf, job, changed):
    assert _decide(tmp_path, wf, job, changed) == "true"


@needs_tools
@pytest.mark.parametrize("wf,job", JOBS)
@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_push_and_dispatch_always_run_in_full(tmp_path, wf, job, event):
    assert _decide(tmp_path, wf, job, ["mobile/lib/main.dart"], event=event) == "true"


@pytest.mark.parametrize("wf,job", JOBS)
def test_every_step_after_the_decision_is_gated(wf, job):
    steps = _steps(wf, job)
    idx = [s.get("id") for s in steps].index("changes")
    assert idx == 1 and "actions/checkout" in steps[0]["uses"] and steps[0]["with"]["fetch-depth"] == 0
    later = steps[idx + 1:]
    assert later and all("steps.changes.outputs.build == 'true'" in str(s.get("if", "")) for s in later)


def test_audio_routing_keeps_its_steps_and_cancelled_semantics():
    steps = _steps("audio-routing.yml", "audio-routing")
    assert len(steps) == 13  # 12 before #976 + the decide step
    assert sum("!cancelled()" in str(s.get("if", "")) for s in steps) == 6


def test_acceptance_still_fails_when_the_table_fails():
    steps = _steps("acceptance-windows.yml", "acceptance")
    fail = next(s for s in steps if s.get("name") == "Fail if acceptance failed")
    assert "steps.acceptance.outcome == 'failure'" in fail["if"] and fail["run"] == "exit 1"


@needs_tools
@pytest.mark.parametrize("wf,job", JOBS)
@pytest.mark.parametrize("src,dst", [("tinglongwu/core.py", "mobile/core.py"), ("scripts/ci_e2e.py", "mobile/ci_e2e.py")])
def test_desktop_file_moved_into_mobile_runs_in_full(tmp_path, wf, job, src, dst):
    """Rename detection would list only the mobile/ destination; the desktop deletion must still count."""
    assert _decide(tmp_path, wf, job, [], moves=[(src, dst)]) == "true"


@needs_tools
@pytest.mark.parametrize("wf,job", JOBS)
def test_large_file_list_with_an_early_desktop_path_runs_in_full(tmp_path, wf, job):
    """GitHub runs `shell: bash` with pipefail; grep must read all input so echo never dies of SIGPIPE."""
    changed = [".github/workflows/mobile-apk.yml"] + [f"mobile/lib/gen/f{i:05d}_{'x' * 60}.dart" for i in range(3000)]
    assert _decide(tmp_path, wf, job, changed) == "true"
