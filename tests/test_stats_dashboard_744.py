"""#744: stats page shows quota minutes/day, PRs merged/day + PRs per 100 quota min, and release time."""
import json
import re
import sys
from datetime import datetime, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import stats_dashboard as sd  # noqa: E402

NOW = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)  # 18:00 Asia/Taipei


def _pr(number, merged_at, model="sonnet", minutes=10):
    body = "x\n\ntlwu-usage: model=%s tool_calls=3 minutes=%s" % (model, minutes) if model else "no usage line"
    return {"number": number, "created_at": "2026-11-01T00:00:00Z", "merged_at": merged_at, "body": body}


def test_quota_and_merged_per_taipei_day_with_weights_and_marker():
    prs = [_pr(1, "2026-11-14T01:00:00Z", "sonnet", 10),  # 11-14 09:00 TPE
           _pr(2, "2026-11-14T02:00:00Z", "opus", 15),  # opus counts 2x -> 30
           _pr(743, "2026-11-13T17:30:00Z", "sonnet", 5),  # UTC 11-13 but 11-14 01:30 in Taipei; the marker PR
           _pr(4, "2026-11-13T15:00:00Z", None),  # 11-13 23:00 TPE, merged but no usage line: 0 quota
           _pr(5, "2026-10-20T00:00:00Z", "sonnet", 99)]  # outside the 14-day window
    tr = sd.quota_trend(prs, NOW)
    assert len(tr["days"]) == 14 and tr["days"][-1] == "2026-11-14" and tr["days"][0] == "2026-11-01"
    assert tr["quota"][-1] == 45.0 and tr["merged"][-1] == 3
    assert tr["quota"][-2] == 0.0 and tr["merged"][-2] == 1 and tr["per100"][-2] is None
    assert tr["per100"][-1] == round(100 * 3 / 45, 2)
    assert sum(tr["merged"]) == 4 and sum(tr["quota"]) == 45.0
    assert tr["marker"] == "2026-11-14"
    assert sd.quota_trend(prs[:2], NOW)["marker"] is None


RELS = [{"tag": "v0.3.4", "downloads": 1, "published_at": "2026-11-14T10:16:22Z"},
        {"tag": "v0.3.3", "downloads": 1, "published_at": "2026-11-14T05:25:34Z"},
        {"tag": "draft", "downloads": 0}]
RUNS = [{"conclusion": "success", "run_started_at": "2026-11-14T09:17:21Z", "updated_at": "2026-11-14T10:16:28Z"},
        {"conclusion": "success", "run_started_at": "2026-11-14T08:00:00Z", "updated_at": "2026-11-14T08:01:00Z"},
        {"conclusion": "failure", "run_started_at": "2026-11-14T04:00:00Z", "updated_at": "2026-11-14T05:30:00Z"},
        {"conclusion": "success", "run_started_at": "2026-11-14T04:20:35Z", "updated_at": "2026-11-14T05:25:40Z"}]
ITEMS = [{"number": 817, "title": "release: bump __version__ to 0.3.4", "created_at": "2026-11-14T08:00:00Z",
          "pull_request": {"merged_at": "2026-11-14T08:31:28Z"}},
         {"number": 820, "title": "release: v0.3.4", "created_at": "2026-11-14T08:40:00Z",
          "pull_request": {"merged_at": "2026-11-14T09:17:00Z"}},
         {"number": 830, "title": "release: v0.3.4", "created_at": "2026-11-14T09:26:19Z",
          "pull_request": {"merged_at": None}},  # stale reopen after the merge: not the one that shipped
         {"number": 790, "title": "release: v0.3.3", "created_at": "2026-11-14T03:25:34Z", "pull_request": {}},
         {"number": 5, "title": "release: v0.3.3", "created_at": "2026-11-14T03:00:00Z", "state": "open",
          "closed_at": None, "labels": []}]  # an issue, not a PR


def test_release_time_pairs_run_and_release_pr():
    rows = sd.release_times(RELS, RUNS, ITEMS)
    assert [r["tag"] for r in rows] == ["v0.3.4", "v0.3.3"]  # drafts / unpublished left out, newest first
    assert rows[0]["run_min"] == 59.1 and rows[0]["pr"] == 820 and rows[0]["pr_min"] == 96.4
    assert rows[1]["run_min"] == 65.1 and rows[1]["pr"] == 790 and rows[1]["pr_min"] == 120.0
    assert sd.release_times(RELS, None, [])[0] == {"tag": "v0.3.4", "published_at": "2026-11-14T10:16:22Z",
                                                   "run_min": None, "pr": None, "pr_min": None}
    many = [{"tag": "v%d" % i, "published_at": "2026-11-%02dT00:00:00Z" % (i + 1)} for i in range(12)]
    assert [r["tag"] for r in sd.release_times(many, [], [])] == ["v%d" % i for i in range(11, 1, -1)]


def test_page_renders_the_three_blocks(tmp_path):
    def api(path, accept=None):
        if path == "/repos/o/r":
            return {"stargazers_count": 0, "forks_count": 0, "created_at": "2026-11-12T00:00:00Z"}
        if "/releases" in path:
            return [{"tag_name": "v0.3.4", "published_at": "2026-11-14T10:16:22Z", "assets": []}]
        if "/actions/workflows/release.yml/runs" in path:
            return {"workflow_runs": RUNS}
        if "/pulls?" in path:
            return [_pr(743, "2026-11-14T05:29:28Z", "opus", 20)]
        if "/issues?state=all" in path:
            return ITEMS
        return []

    sd.run(str(tmp_path), "o/r", "dev", api=api, now=NOW)
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    for h in ("Quota minutes per day", "PRs merged per 100 quota minutes", "release.yml run", 'id="c_quota"',
              'id="c_per100"', 'id="c_run"', 'id="c_prt"'):
        assert h in page, h
    data = json.loads(re.search(r'<script id="d" type="application/json">(.*?)</script>', page, re.S).group(1))
    assert data["quota_trend"]["marker"] == "2026-11-14" and data["quota_trend"]["marker_pr"] == 743
    assert data["release_times"][0]["tag"] == "v0.3.4" and data["release_times"][0]["pr"] == 820
    series = json.loads((tmp_path / "series.json").read_text())
    assert series["quota_trend"]["quota"][-1] == 40.0 and series["release_times"][0]["run_min"] == 59.1
