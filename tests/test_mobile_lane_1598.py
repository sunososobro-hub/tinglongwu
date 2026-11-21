"""#1598: mobile release lane decision (scripts/mobile_lane.py)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import mobile_lane as ml  # noqa: E402

GREEN = {"analyze": "success", "test": "success", "aab": "success"}


def test_green_snapshot_tags_and_targets_production_draft():
    d = ml.decide("release/v0.4.11", GREEN)
    assert (d["tag"], d["track"], d["version"]) == ("mobile-v0.4.11", "production", "0.4.11")
    assert d["status"] == "draft"
    assert d["release_note"] == "mobile 0.4.11 drafted for production"


@pytest.mark.parametrize("gate", ml.GATES)
def test_any_red_gate_means_no_tag_no_upload(gate):
    d = ml.decide("release/v0.4.11", dict(GREEN, **{gate: "failure"}), "mobile-v0.4.10")
    assert d["tag"] is None and d["track"] is None and d["failed_gates"] == [gate]
    assert d["release_note"] == "skipped (not verified), stays on mobile-v0.4.10"


def test_missing_gate_result_counts_as_red():
    assert ml.decide("release/v1.0.0", {"analyze": "success"})["tag"] is None


def test_production_is_only_ever_a_draft():
    d = ml.decide("release/v1.0.0", GREEN)
    assert (d["track"], d["status"]) == ("production", "draft")
    assert ml.decide("release/v1.0.0", dict(GREEN, test="failure"))["status"] is None


@pytest.mark.parametrize("bad", ["dev", "release/v1.0", "release/v1.0.0-rc1", "hotfix/v1.0.1", "", None])
def test_only_snapshot_branches_accepted(bad):
    with pytest.raises(ValueError):
        ml.decide(bad, GREEN)


def run_cli(*args):
    return subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "mobile_lane.py"), *args],
                          capture_output=True, text=True)


def test_cli_green_dry_run_logs_track_production_draft():
    r = run_cli("--branch", "release/v0.4.11", "--analyze", "success", "--test", "success",
                "--aab", "success", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "track=production status=draft" in r.stdout
    assert json.loads(r.stdout.splitlines()[0])["dry_run"] is True


def test_cli_failing_flutter_test_skips():
    r = run_cli("--branch", "release/v0.4.11", "--analyze", "success", "--test", "failure", "--aab", "success")
    assert r.returncode == 1
    assert "track=" not in r.stdout
    assert json.loads(r.stdout)["tag"] is None


def test_cli_bad_branch_is_error():
    assert run_cli("--branch", "dev").returncode == 3


def test_cli_out_file_acts_only_when_green_and_not_dry_run(tmp_path):
    def outputs(*extra, test="success"):
        out = tmp_path / ("o%d.txt" % len(list(tmp_path.iterdir())))
        run_cli("--branch", "release/v0.4.11", "--analyze", "success", "--test", test,
                "--aab", "success", "--out", str(out), *extra)
        return dict(l.split("=", 1) for l in out.read_text().splitlines())

    live = outputs()
    assert (live["act"], live["tag"], live["track"], live["status"]) == ("true", "mobile-v0.4.11", "production", "draft")
    assert outputs("--dry-run")["act"] == "false"
    red = outputs(test="failure")
    assert (red["act"], red["tag"], red["track"]) == ("false", "", "")
    assert "skipped (not verified)" in red["release_note"]


def test_mobile_release_workflow_is_valid_yaml_with_lane_job():
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "mobile-release.yml").read_text())
    assert "lane" in wf["jobs"] and wf["jobs"]["lane"]["permissions"]["contents"] == "write"


def test_release_notes_mobile_line():
    import release_notes as rn
    tags = ["mobile-v0.4.10", "mobile-v0.5.0"]
    assert rn.mobile_md(rn.mobile_state("v0.5.0", tags)) == "- Mobile: mobile 0.5.0 drafted for production"
    assert rn.mobile_md(rn.mobile_state("v0.5.1", tags)) == "- Mobile: skipped (not verified), stays on mobile-v0.5.0"
    assert rn.mobile_md(rn.mobile_state("v0.4.9", tags)) == "- Mobile: skipped (not verified), no earlier mobile release"
    plan = {p: {"ship": True, "version": "v0.5.1", "move_to": ""} for p in ("windows", *rn.MARKERS)}
    plan["mobile"] = rn.mobile_state("v0.5.1", tags)
    assert "- Mobile: skipped (not verified), stays on mobile-v0.5.0" in rn.platforms_md(plan)
