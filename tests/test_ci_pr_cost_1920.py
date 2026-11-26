import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("ci_pr_cost", Path(__file__).parents[1] / "scripts" / "ci_pr_cost.py")
cost = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cost)

PR = {"number": 7, "created_at": "2026-11-25T10:00:00Z", "merged_at": "2026-11-25T11:00:00Z", "head": {"sha": "bbb"}}


def _run(i, name, sha, created, started, updated, event="pull_request"):
    return {"id": i, "name": name, "head_sha": sha, "event": event, "created_at": created,
            "run_started_at": started, "updated_at": updated}


def _job(start, end):
    return {"started_at": start, "completed_at": end}


def test_pr_cost_counts_only_pr_runs_in_window_and_head_ci():
    runs = [
        _run(1, "ci", "aaa", "2026-11-25T10:01:00Z", "2026-11-25T10:01:00Z", "2026-11-25T10:03:00Z"),  # superseded
        _run(2, "ci", "bbb", "2026-11-25T10:10:00Z", "2026-11-25T10:10:00Z", "2026-11-25T10:18:00Z"),  # head
        _run(3, "e2e", "bbb", "2026-11-25T10:10:00Z", "2026-11-25T10:10:00Z", "2026-11-25T10:15:00Z"),
        _run(4, "ci", "bbb", "2026-11-25T12:00:00Z", "2026-11-25T12:00:00Z", "2026-11-25T12:30:00Z"),  # after merge
        _run(5, "ci", "bbb", "2026-11-25T10:20:00Z", "2026-11-25T10:20:00Z", "2026-11-25T10:50:00Z", event="push"),
    ]
    jobs = {
        1: [_job("2026-11-25T10:01:00Z", "2026-11-25T10:03:00Z")],
        2: [_job("2026-11-25T10:10:00Z", "2026-11-25T10:16:00Z"), _job("2026-11-25T10:10:00Z", "2026-11-25T10:18:00Z"),
            _job(None, None)],  # skipped job: 0 min
        3: [_job("2026-11-25T10:10:00Z", "2026-11-25T10:15:00Z")],
        4: [_job("2026-11-25T12:00:00Z", "2026-11-25T12:30:00Z")],
        5: [_job("2026-11-25T10:20:00Z", "2026-11-25T10:50:00Z")],
    }
    c = cost.pr_cost(PR, runs, jobs)
    assert c == {"number": 7, "ci_min": 8.0, "job_min": 2 + 6 + 8 + 5, "ci_runs": 2}


def test_pr_cost_without_head_ci_run():
    assert cost.pr_cost(PR, [], {})["ci_min"] is None


def test_summarize_medians_and_multi_runs():
    costs = [
        {"number": 1, "ci_min": 7.0, "job_min": 40.0, "ci_runs": 1},
        {"number": 2, "ci_min": 9.0, "job_min": 50.0, "ci_runs": 3},
        {"number": 3, "ci_min": None, "job_min": 3.0, "ci_runs": 0},
    ]
    assert cost.summarize(costs) == {"prs": 3, "ci_median": 8.0, "job_median": 40.0, "job_mean": 31.0,
                                     "multi_ci": 1, "max_ci_runs": 3}
