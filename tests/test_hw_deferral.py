"""#848: auto-release must not stall when the home hw-tests runner (tlwu-hw) is offline.
Behaviour of scripts/hw_deferral.py and its use in scripts/release_notes.py (auto gate, release.yml plan, notes):
runner offline -> deferred after HW_RUNNER_WAIT_MIN; runner online -> unchanged; hw failed later -> P0 issue."""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hd = load("hw_deferral_848", "scripts/hw_deferral.py")
rn = load("release_notes_848", "scripts/release_notes.py")
REPO = "sunososobro-hub/tinglongwu"
HEAD, LATER, OLD = "b" * 40, "c" * 40, "a" * 40
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
TAGS = {"v0.3.1": "4" * 40}
WN = "## v0.3.2\n- Faster subtitles\n"
OPEN = {"number": 735, "title": "release: v0.3.2", "state": "open", "base": {"ref": "main"},
        "head": {"ref": "dev", "sha": HEAD, "repo": {"full_name": REPO}}, "body": "## What's new\n- x\n"}


def iso(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def cloud_green():
    return [{"id": i, "name": n, "status": "completed", "conclusion": "success", "html_url": f"https://x/{n}",
             "created_at": iso(60)} for i, n in enumerate(hd.CLOUD_CHECKS)]


def hw(status="queued", conclusion=None, minutes_ago=hd.HW_RUNNER_WAIT_MIN + 1, rid=50):
    return {"id": rid, "name": "hw-tests", "status": status, "conclusion": conclusion, "html_url": "https://x/hw",
            "created_at": iso(minutes_ago)}


QUEUED_JOB = [{"id": 1, "status": "queued", "runner_name": None, "steps": []}]
RUNNING_JOB = [{"id": 1, "status": "in_progress", "runner_name": "tlwu-hw", "steps": [{"name": "checkout"}]}]


def auto(runs, jobs=QUEUED_JOB):
    return rn.auto_plan("true", "level: 2", HEAD, "0.3.2", TAGS, [OPEN], REPO, lambda h: runs if h == HEAD else [],
                        WN, [], lambda rid: jobs, NOW)


def test_wait_constant_is_30_minutes():
    assert hd.HW_RUNNER_WAIT_MIN == 30


# ---------------------------------------------------------------- runner offline

def test_offline_runner_defers_hw_tests_after_the_wait_and_merges_on_cloud_checks():
    out = auto([*cloud_green(), hw()])
    assert out["merge"] == "true" and out["hw_deferred"] == "true" and out["tag"] == "v0.3.2"
    assert "hardware checks deferred" in out["reason"]


def test_offline_runner_waits_before_the_limit():
    out = auto([*cloud_green(), hw(minutes_ago=hd.HW_RUNNER_WAIT_MIN - 1)])
    assert out["merge"] == "false" and "not started after 29 min" in out["reason"]
    assert hd.decide([hw(minutes_ago=hd.HW_RUNNER_WAIT_MIN)], lambda r: QUEUED_JOB, NOW)["state"] == "deferred"


@pytest.mark.parametrize("status", ["queued", "pending", "waiting"])
def test_not_started_states_defer(status):
    assert hd.decide([hw(status=status)], lambda r: QUEUED_JOB, NOW)["state"] == "deferred"


def test_run_cancelled_while_waiting_for_a_runner_counts_as_not_started():
    assert hd.decide([hw("completed", "cancelled")], lambda r: QUEUED_JOB, NOW)["state"] == "deferred"


def test_deferral_still_needs_every_cloud_check():
    runs = [r if r["name"] != "e2e" else {**r, "conclusion": "failure"} for r in cloud_green()] + [hw()]
    out = auto(runs)
    assert out["merge"] == "false" and "e2e: completed/failure" in out["reason"]


def test_no_hw_run_or_no_timestamp_never_defers():
    assert auto(cloud_green())["merge"] == "false"
    assert hd.decide([{**hw(), "created_at": None}], lambda r: QUEUED_JOB, NOW)["state"] == "waiting"


# ---------------------------------------------------------------- runner online: unchanged

def test_online_runner_success_merges_without_deferral():
    out = auto([*cloud_green(), hw("completed", "success")])
    assert out["merge"] == "true" and "hw_deferred" not in out


@pytest.mark.parametrize("run, jobs", [
    (hw("in_progress", minutes_ago=120), RUNNING_JOB),           # running for 2 h: keep waiting for it
    (hw("queued", minutes_ago=120), RUNNING_JOB),                # a runner took the job
    (hw("completed", "failure", minutes_ago=120), RUNNING_JOB),  # it ran and failed
    (hw("completed", "failure", minutes_ago=120), []),           # failed, jobs unknown: never ship over it
    (hw("completed", "cancelled", minutes_ago=120), RUNNING_JOB),  # cancelled mid-run
])
def test_started_hw_tests_is_never_deferred(run, jobs):
    out = auto([*cloud_green(), run], jobs)
    assert out["merge"] == "false" and "hw-tests:" in out["reason"]
    assert hd.decide([run], lambda r: jobs, NOW)["state"] == "started"


# ---------------------------------------------------------------- release.yml plan and notes

def merged_pr(body):
    return {**OPEN, "merged_at": "2026-11-14T12:01:00Z", "body": body}


def plan(body, runs, jobs=QUEUED_JOB):
    return rn.plan("push", "main", "d" * 40, "0.3.2", TAGS, [merged_pr(body)], REPO,
                   lambda h: runs if h == HEAD else [], jobs_of=lambda rid: jobs)


def test_plan_publishes_a_deferred_release_marked_by_auto_release():
    body = hd.deferred_body(OPEN["body"])
    assert hd.deferred_body(body) == body  # idempotent
    p = plan(body, [*cloud_green(), hw()])
    assert (p["publish"], p["hw_deferred"], p["hw_run"]) == ("true", "true", "")
    # the runner came back after the merge and hw-tests is running: still publish (followup watches the result)
    assert plan(body, [*cloud_green(), hw("in_progress")], RUNNING_JOB)["publish"] == "true"


def test_plan_refuses_without_marker_or_after_a_hw_failure():
    with pytest.raises(SystemExit, match="hw-tests"):
        plan(OPEN["body"], [*cloud_green(), hw()])
    with pytest.raises(SystemExit, match="hw-tests: completed/failure"):
        plan(hd.deferred_body(OPEN["body"]), [*cloud_green(), hw("completed", "failure")], RUNNING_JOB)


def test_release_notes_say_hardware_checks_deferred():
    base = "## What's new\n- TODO: x\n"
    notes = rn.release_notes(base, "", None, "ok", "", HEAD, [], {}, None, WN, "v0.3.2", hw_deferred=True)
    assert sum("hardware checks deferred" in line for line in notes.splitlines()) == 1
    normal = rn.release_notes(base, "", None, "ok", "https://x/hw", HEAD, [], {}, None, WN, "v0.3.2")
    assert "deferred" not in normal and "[hw-tests](https://x/hw)" in normal


# ---------------------------------------------------------------- hw-tests failed later

ORDER = [OLD, HEAD, LATER]  # dev history: OLD -> HEAD (released) -> LATER


def in_range(sha, failed):
    return sha in ORDER and ORDER.index(HEAD) <= ORDER.index(sha) <= ORDER.index(failed)


@pytest.mark.parametrize("failed", [HEAD, LATER])
def test_hw_failure_after_a_deferred_release_needs_a_p0_issue(failed):
    pr = merged_pr(hd.deferred_body(""))
    title = hd.deferred_failure_title(pr, failed, [], lambda s: in_range(s, failed))
    assert title == "[release] deferred hw-tests failed on v0.3.2"


def test_no_issue_when_not_deferred_already_verified_or_before_the_release():
    deferred = merged_pr(hd.deferred_body(""))
    assert hd.deferred_failure_title(merged_pr("plain"), LATER, [], lambda s: in_range(s, LATER)) is None
    ok = [{"head_sha": HEAD, "conclusion": "success"}]
    assert hd.deferred_failure_title(deferred, LATER, ok, lambda s: in_range(s, LATER)) is None
    assert hd.deferred_failure_title(deferred, OLD, [], lambda s: in_range(s, OLD)) is None
    assert hd.deferred_failure_title(None, LATER, [], lambda s: True) is None


def test_followup_opens_one_p0_issue_and_does_not_duplicate(monkeypatch):
    posted, open_issues = [], []

    def gh(*args):
        url = next(a for a in args if a.startswith("repos/"))
        if "pulls?state=closed" in url:
            return [merged_pr(hd.deferred_body("")), {**merged_pr(""), "merged_at": "2026-11-01T00:00:00Z"}]
        if "hw-tests.yml/runs" in url:
            return {"workflow_runs": [{"head_sha": OLD, "conclusion": "success"}]}
        if "issues?state=open" in url:
            return open_issues
        posted.append(args)
        return {"number": 900}
    monkeypatch.setattr(hd, "_gh_json", gh)
    monkeypatch.setattr(hd, "_ancestor", lambda a, b: ORDER.index(a) <= ORDER.index(b))
    assert hd.followup(REPO, LATER, "https://x/run/1") == "opened #900 [release] deferred hw-tests failed on v0.3.2"
    fields = [a for a in posted[0] if a.startswith(("title=", "labels[]="))]
    assert "title=[release] deferred hw-tests failed on v0.3.2" in fields and "labels[]=P0" in fields
    open_issues.append({"number": 900, "title": "[release] deferred hw-tests failed on v0.3.2"})
    assert "already open" in hd.followup(REPO, LATER, "https://x/run/2") and len(posted) == 1


def test_decide_cli_dry_run_with_mocked_runs(tmp_path, capsys):
    (tmp_path / "runs.json").write_text(json.dumps([*cloud_green(), hw()]), "utf-8")
    (tmp_path / "jobs.json").write_text(json.dumps({"50": QUEUED_JOB}), "utf-8")
    assert hd.main(["decide", "--runs", str(tmp_path / "runs.json"), "--jobs", str(tmp_path / "jobs.json"),
                    "--now", NOW.isoformat()]) == 0
    assert "state=deferred" in capsys.readouterr().out
    (tmp_path / "jobs.json").write_text(json.dumps({"50": RUNNING_JOB}), "utf-8")
    hd.main(["decide", "--runs", str(tmp_path / "runs.json"), "--jobs", str(tmp_path / "jobs.json"),
             "--now", NOW.isoformat()])
    assert "state=started" in capsys.readouterr().out
