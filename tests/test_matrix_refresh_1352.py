"""#1352: per-release refresh of the 6x6 traffic-light matrix (scripts/bench/matrix_refresh.py)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("matrix_refresh", ROOT / "scripts" / "bench" / "matrix_refresh.py")
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)

TAG = "v0.4.6"


def _run(i, status="completed", conclusion="success", branch=TAG):
    return {"id": i, "status": status, "conclusion": conclusion, "head_branch": branch,
            "html_url": f"https://example.com/runs/{i}"}


def test_no_run_on_the_tag_dispatches():
    assert mr.decide(TAG, [], {}, False)[0] == "dispatch"
    # a 6x6 run on dev (other branch) does not count for the release
    assert mr.decide(TAG, [_run(1, branch="dev")], {1: [mr.TABLE_ARTIFACT]}, False)[0] == "dispatch"


def test_existing_issue_means_done_whatever_the_runs():
    assert mr.decide(TAG, [_run(1, status="in_progress", conclusion=None)], {}, True)[0] == "done"


def test_running_run_waits_and_never_stacks_a_second():
    action, run, _ = mr.decide(TAG, [_run(2, status="queued", conclusion=None), _run(1)], {1: [mr.TABLE_ARTIFACT]},
                               False)
    assert (action, run["id"]) == ("wait", 2)


def test_green_run_with_table_opens_the_issue():
    action, run, _ = mr.decide(TAG, [_run(3), _run(2, conclusion="failure")], {3: ["flores-rus_Cyrl", mr.TABLE_ARTIFACT]},
                               False)
    assert (action, run["id"]) == ("issue", 3)


def test_green_run_without_table_is_not_a_6x6_run():
    # e.g. a plain langs= run dispatched on the tag by hand: dispatch the matrix instead
    assert mr.decide(TAG, [_run(4)], {4: ["flores-rus_Cyrl"]}, False)[0] == "dispatch"


def test_failures_retry_then_report():
    one = [_run(5, conclusion="failure")]
    assert mr.decide(TAG, one, {}, False)[0] == "dispatch"
    two = [_run(6, conclusion="cancelled"), _run(5, conclusion="failure")]
    action, run, _ = mr.decide(TAG, two, {}, False)
    assert (action, run["id"]) == ("failed", 6)


def test_issue_body_has_the_issue_format_and_table():
    table = "| source \\ target | en |\n|---|---|\n\n17 green / 7 yellow / 6 red"
    body = mr.issue_body(TAG, _run(44, conclusion="success"), table)
    for key in ("Why:", "Goal:", "Acceptance:", "Verify:", "Out of scope:"):
        assert key in body
    assert "17 green / 7 yellow / 6 red" in body and "matrix_6x6_44.json" in body and TAG in body
    failed = mr.issue_body(TAG, _run(45, conclusion="failure"), None)
    assert "failed" in failed and "Table from run" not in failed
    assert mr.title(TAG) == "[eval] refresh the 6x6 matrix in docs/benchmarks.md for v0.4.6"


def test_main_dispatches_on_the_release_tag(monkeypatch, capsys):
    calls = []

    def fake_gh(*args, check=True):
        calls.append(args)
        if args[1].endswith("/releases/latest"):
            return TAG
        if "workflows/eval-flores.yml/runs" in args[1]:
            return json.dumps([])
        if args[1] == "-X":  # search
            return "[]"
        return ""

    monkeypatch.setattr(mr, "_gh", fake_gh)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert mr.main(["--repo", "o/r"]) == 0
    assert ("workflow", "run", "eval-flores.yml", "--repo", "o/r", "--ref", TAG, "-f", "matrix=6x6") in calls
    assert "dispatch:" in capsys.readouterr().out


def test_main_opens_the_issue_with_the_downloaded_table(monkeypatch):
    calls = []

    def fake_gh(*args, check=True):
        calls.append(args)
        if args[1].endswith("/releases/latest"):
            return TAG
        if "workflows/eval-flores.yml/runs" in args[1]:
            return json.dumps([_run(9)])
        if args[1].endswith("/runs/9/artifacts"):
            return json.dumps([mr.TABLE_ARTIFACT])
        if args[1] == "-X":
            return "[]"
        if args[:2] == ("run", "download"):
            Path(args[args.index("-D") + 1], "table.md").write_text("T 17 green", encoding="utf-8")
        if args[:2] == ("issue", "create"):
            calls.append(("body", Path(args[args.index("--body-file") + 1]).read_text("utf-8")))
        return ""

    monkeypatch.setattr(mr, "_gh", fake_gh)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert mr.main(["--repo", "o/r"]) == 0
    create = next(c for c in calls if c[:2] == ("issue", "create"))
    assert mr.title(TAG) in create and "status:todo" in create
    body = next(c[1] for c in calls if c[0] == "body")
    assert "T 17 green" in body and "run 9" in body
    assert not any(c[:2] == ("workflow", "run") for c in calls)


def test_dry_run_changes_nothing(monkeypatch):
    calls = []

    def fake_gh(*args, check=True):
        calls.append(args)
        return TAG if args[1].endswith("/releases/latest") else "[]"

    monkeypatch.setattr(mr, "_gh", fake_gh)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert mr.main(["--repo", "o/r", "--dry-run"]) == 0
    assert not any(c[0] in ("workflow", "issue") for c in calls)
