"""#1823: mobile-apk falls back to ubuntu when the tlwu-mobile home runner has not picked up the build in 5 min."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "mobile_runner_fallback.py"
_spec = importlib.util.spec_from_file_location("mrf", SCRIPT)
mrf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mrf)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_queued_build_is_not_picked_up():
    assert not mrf.picked_up([{"name": "build", "status": "queued", "runner_name": None}])
    assert not mrf.picked_up([])
    assert not mrf.picked_up([{"name": "runner-fallback", "status": "in_progress", "runner_name": "GitHub Actions 1"}])


def test_started_or_finished_build_is_picked_up():
    assert mrf.picked_up([{"name": "build", "status": "in_progress", "runner_name": "tlwu-mobile"}])
    assert mrf.picked_up([{"name": "build", "status": "queued", "runner_name": "tlwu-mobile"}])
    assert mrf.picked_up([{"name": "build", "status": "completed", "conclusion": "cancelled"}])


def test_runner_offline_falls_back_after_wait():
    clock = FakeClock()
    calls = []

    def jobs():
        calls.append(clock.t)
        return [{"name": "build", "status": "queued"}]

    ok, waited = mrf.watch(jobs, wait_min=5, poll_s=30, clock=clock, sleep=clock.sleep)
    assert (ok, waited) == (False, 5)
    assert clock.t == 300 and calls[-1] == 300   # last check right before giving up


def test_runner_online_stays_self_hosted():
    clock = FakeClock()

    def jobs():
        status = "in_progress" if clock.t >= 60 else "queued"
        return [{"name": "build", "status": status, "runner_name": "tlwu-mobile" if clock.t >= 60 else None}]

    ok, waited = mrf.watch(jobs, wait_min=5, poll_s=30, clock=clock, sleep=clock.sleep)
    assert ok and waited == 1 and clock.t == 60


def test_dispatch_body_keeps_event_and_forces_ubuntu():
    b = mrf.dispatch_body("fix/12", "pull_request", True, 5, "abc123")
    assert b == {"ref": "fix/12", "inputs": {"runner": "ubuntu", "fallback_after": "5", "fallback_event": "pull_request",
                                             "source_sha": "abc123", "x86_64_debug": True}}


def _fake_gh(tmp_path, jobs_json):
    """A `gh` on PATH that logs its args + stdin and answers the jobs call."""
    log = tmp_path / "gh.log"
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/usr/bin/env python3\nimport sys, json\n"
        f"open({str(log)!r}, 'a').write(json.dumps([sys.argv[1:], '' if sys.stdin.isatty() else sys.stdin.read()]) + '\\n')\n"
        f"print({jobs_json!r} if any('/jobs' in a for a in sys.argv) else '')\n")
    gh.chmod(0o755)
    return log


def _run(tmp_path, jobs, wait_min="0"):
    log = _fake_gh(tmp_path, json.dumps({"jobs": jobs}))
    env = dict(os.environ, PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}", REPO="o/r", RUN_ID="77",
               REF="fix/9", EVENT="pull_request", SOURCE_SHA="merge123", X86="false", WAIT_MIN=wait_min)
    r = subprocess.run([sys.executable, "-I", str(SCRIPT), "watch"], env=env, capture_output=True, text=True)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    return r, calls


def test_cli_offline_dispatches_then_cancels(tmp_path):
    r, calls = _run(tmp_path, [{"name": "build", "status": "queued"}])
    assert r.returncode == 0, r.stderr
    assert "runner fallback: ubuntu after 0 min" in r.stdout
    args = [c[0] for c in calls]
    assert args[-2][:3] == ["api", "-X", "POST"] and args[-2][3] == "repos/o/r/actions/workflows/mobile-apk.yml/dispatches"
    sent = json.loads(calls[-2][1])
    assert sent["ref"] == "fix/9" and sent["inputs"]["runner"] == "ubuntu"
    assert sent["inputs"]["source_sha"] == "merge123"   # CodeRabbit #1842: build the PR merge commit, not the branch tip
    assert args[-1][3] == "repos/o/r/actions/runs/77/cancel"


def test_cli_online_does_nothing(tmp_path):
    r, calls = _run(tmp_path, [{"name": "build", "status": "in_progress", "runner_name": "tlwu-mobile"}], "5")
    assert r.returncode == 0 and "no fallback" in r.stdout
    assert all("POST" not in c[0] for c in calls)


def test_workflow_wiring():
    wf = yaml.safe_load((ROOT / ".github/workflows/mobile-apk.yml").read_text(encoding="utf-8"))
    on = wf.get("on", wf.get(True))
    inputs = on["workflow_dispatch"]["inputs"]
    assert {"runner", "fallback_after", "fallback_event", "source_sha"} <= set(inputs)
    checkout = next(s for s in wf["jobs"]["build"]["steps"] if "actions/checkout" in (s.get("uses") or ""))
    assert checkout["with"]["ref"] == "${{ inputs.source_sha || github.sha }}"
    assert "inputs.runner != 'ubuntu'" in wf["jobs"]["build"]["runs-on"]
    fb = wf["jobs"]["runner-fallback"]
    assert fb["runs-on"] == "ubuntu-24.04" and fb["permissions"]["actions"] == "write"
    assert "inputs.runner != 'ubuntu'" in fb["if"] and "vars.MOBILE_RUNNER == 'tlwu-mobile'" in fb["if"]
