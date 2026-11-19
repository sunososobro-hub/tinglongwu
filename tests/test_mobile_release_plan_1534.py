"""#1534: mobile promotion plan (mobile/tool/mobile_release_plan.py), dry-run gates."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "mobile" / "tool"
sys.path.insert(0, str(TOOL))
import mobile_release_plan as rp  # noqa: E402

PUBSPEC = "name: m\nversion: 1.0.1+9\n"
NOTES = "# What's new\n\n## mobile 1.0.1\n- Faster Russian subtitles.\n\n## mobile 1.0.0\n- First.\n"


def run(n, conclusion="success", event="push", status="completed"):
    return {"run_number": n, "conclusion": conclusion, "event": event, "status": status,
            "head_sha": "sha%d" % n}


def test_picks_newest_green_push_run():
    runs = [run(10), run(12, "failure"), run(11), run(13, event="pull_request"), run(14, status="in_progress")]
    assert rp.newest_green_build(runs)["run_number"] == 11


def test_all_gates_pass():
    plan = rp.build_plan([run(5)], PUBSPEC, NOTES, True)
    assert plan["blockers"] == []
    assert (plan["build_run"], plan["sha"], plan["versionName"]) == (5, "sha5", "1.0.1")
    assert "Russian" in plan["notes"]
    assert plan["aabBuildName"] == "1.0.1-dev.5"  # notes still keyed on the base version


def test_kill_switch_blocks():
    plan = rp.build_plan([run(5)], PUBSPEC, NOTES, False)
    assert any("MOBILE_AUTO_RELEASE" in b for b in plan["blockers"])


def test_missing_notes_and_no_green_run_block():
    plan = rp.build_plan([run(5, "failure")], PUBSPEC, "# What's new\n", True)
    assert len(plan["blockers"]) == 2


def test_todo_notes_block():
    bad = NOTES.replace("Faster Russian subtitles.", "TODO write")
    assert rp.build_plan([run(5)], PUBSPEC, bad, True)["blockers"]


def test_cli_exit_codes(tmp_path):
    runs = tmp_path / "runs.json"
    runs.write_text(json.dumps({"workflow_runs": [run(7)]}))
    ps, wn = tmp_path / "pubspec.yaml", tmp_path / "wn.md"
    ps.write_text(PUBSPEC)
    wn.write_text(NOTES)
    base = [sys.executable, "-I", str(TOOL / "mobile_release_plan.py"), str(runs),
            "--pubspec", str(ps), "--whats-new", str(wn)]
    ok = subprocess.run(base + ["--auto", "true"], capture_output=True, text=True, cwd=TOOL)
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["versionName"] == "1.0.1"
    off = subprocess.run(base, capture_output=True, text=True, cwd=TOOL)
    assert off.returncode == 1


def test_head_sha_mismatch_blocks_and_match_passes():
    assert rp.build_plan([run(5)], PUBSPEC, NOTES, True, head_sha="sha5")["blockers"] == []
    plan = rp.build_plan([run(5)], PUBSPEC, NOTES, True, head_sha="other")
    assert any("not the selected build" in b for b in plan["blockers"])


def test_cli_missing_file_is_error_not_blocked(tmp_path):
    r = subprocess.run([sys.executable, "-I", str(TOOL / "mobile_release_plan.py"), str(tmp_path / "nope.json")],
                       capture_output=True, text=True, cwd=TOOL)
    assert r.returncode == 3
