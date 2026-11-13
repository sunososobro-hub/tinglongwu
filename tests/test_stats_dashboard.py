import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import stats_dashboard as sd  # noqa: E402

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def fake_api(path, accept=None):
    if path == "/repos/o/r":
        return {"stargazers_count": 7, "forks_count": 2, "created_at": "2026-11-10T08:00:00Z"}
    if "/stargazers" in path:
        assert accept and "star" in accept
        return [{"starred_at": "2026-11-10T09:00:00Z"}, {"starred_at": "2026-11-12T01:00:00Z"}]
    if "/forks" in path:
        return [{"created_at": "2026-11-11T09:00:00Z"}]
    if "/milestones" in path:
        return [{"title": "v1", "open_issues": 1, "closed_issues": 2}]
    if "/pulls?" in path:
        return [{"created_at": "2026-11-11T08:00:00Z", "merged_at": "2026-11-12T08:00:00Z"},
                {"created_at": "2026-11-11T08:00:00Z", "merged_at": None}]
    if "/issues/events" in path:
        return [{"event": "labeled", "label": {"name": "P1"}, "created_at": "2026-11-11T01:00:00Z",
                 "issue": {"number": 1}}]
    if "/issues?state=all" in path:
        return [{"number": 1, "state": "open", "created_at": "2026-11-10T10:00:00Z", "closed_at": None,
                 "labels": [{"name": "P1"}], "milestone": {"title": "v1"}},
                {"number": 2, "state": "closed", "created_at": "2026-11-10T10:00:00Z",
                 "closed_at": "2026-11-11T10:00:00Z", "labels": []}]
    if "/issues?" in path:
        return [{"labels": [{"name": "bug"}]}, {"labels": [{"name": "bug"}, {"name": "P1"}]},
                {"labels": [{"name": "bug"}], "pull_request": {}}]
    if "/commits?" in path:
        return [{"commit": {"committer": {"date": "2026-11-12T11:05:00Z"}}},
                {"commit": {"committer": {"date": "2026-11-12T11:50:00Z"}}},
                {"commit": {"committer": {"date": "2026-11-11T03:00:00Z"}}}]
    raise AssertionError(path)


def test_run_writes_data_and_html_and_keeps_history(tmp_path):
    old = [{"ts": "2026-11-12T10:00:00Z", "stars": 5, "forks": 2, "open_issues": 1,
            "labels": {}, "commits_by_hour": {}}]
    (tmp_path / "data.json").write_text(json.dumps(old))
    hist = sd.run(str(tmp_path), "o/r", "dev", api=fake_api, now=NOW)
    assert [h["stars"] for h in hist] == [5, 7]
    snap = hist[-1]
    assert snap["open_issues"] == 2 and snap["labels"] == {"bug": 2, "P1": 1}
    assert snap["commits_by_hour"]["2026-11-12T11"] == 2
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "<svg" in page and "Open issues by" in page and "2026-11-10" in page
    series = json.loads((tmp_path / "series.json").read_text())
    assert series["days"][0] == "2026-11-10" and series["stars"][-1] == 2
    assert series["forks"] == [0, 1, 1] and series["open_issues"][:3] == [2, 1, 1]
    assert series["groups"]["priority"]["P1"] == [0, 1, 1]  # P1 labeled on 11-11
    assert series["groups"]["milestone"]["v1"][-1] == 1
    assert series["prs_merged"] == [0, 0, 1] and series["issues_closed"] == [0, 1, 0]
    assert series["speed"]["pr_all"] == 24.0 and series["hero"]["prs"]["total"] == 1
    assert "Work finished per day" in page and page.index("Work finished") < page.index("Stars")
    assert json.loads((tmp_path / "data.json").read_text()) == hist


def test_same_hour_replaces_snapshot(tmp_path):
    sd.run(str(tmp_path), "o/r", "dev", api=fake_api, now=NOW)
    hist = sd.run(str(tmp_path), "o/r", "dev", api=fake_api, now=NOW)
    assert len(hist) == 1


DAYS = ["2026-11-09", "2026-11-10", "2026-11-11"]


def test_day_range_and_counts():
    assert sd.day_range("2026-11-29", "2026-12-01") == ["2026-11-29", "2026-11-30", "2026-12-01"]
    ts = ["2026-11-08T23:00:00Z", "2026-11-10T01:00:00Z", "2026-11-10T02:00:00Z"]
    assert sd.per_day(ts, DAYS) == [0, 2, 0]
    assert sd.cumulative(ts, DAYS) == [1, 3, 3]


def test_open_over_time_from_created_closed():
    issues = [{"created_at": "2026-11-09T10:00:00Z", "closed_at": "2026-11-10T09:00:00Z"},
              {"created_at": "2026-11-10T10:00:00Z", "closed_at": None}]
    assert sd.open_over_time(issues, DAYS) == [1, 1, 1]
    issues[0]["closed_at"] = "2026-11-09T12:00:00Z"
    assert sd.open_over_time(issues, DAYS) == [0, 1, 1]


def test_group_of_prefixes():
    assert sd.group_of("area:gui") == ("area", "gui")
    assert sd.group_of("P2") == ("priority", "P2")
    assert sd.group_of("status:todo") == ("status", "todo")
    assert sd.group_of("milestone:v0.4") == ("milestone", "v0.4")
    assert sd.group_of("P7") is None and sd.group_of("bug") is None


def test_tokens_at_reconstructs_history():
    ev = [("2026-11-10T01:00:00Z", "add", "status:doing"),
          ("2026-11-10T01:00:00Z", "remove", "status:todo"),
          ("2026-11-11T05:00:00Z", "add", "P0")]
    final = {"status:doing", "P0"}
    assert sd.tokens_at(final, ev, "2026-11-09") == {"status:todo"}
    assert sd.tokens_at(final, ev, "2026-11-10") == {"status:doing"}
    assert sd.tokens_at(final, ev, "2026-11-11") == {"status:doing", "P0"}


def test_collect_events_skips_prs_and_group_series():
    raw = [{"event": "milestoned", "milestone": {"title": "v1"}, "created_at": "2026-11-10T01:00:00Z",
            "issue": {"number": 1}},
           {"event": "labeled", "label": {"name": "bug"}, "created_at": "2026-11-10T01:00:00Z",
            "issue": {"number": 9, "pull_request": {}}},
           {"event": "closed", "created_at": "2026-11-10T01:00:00Z", "issue": {"number": 1}}]
    tev = sd.collect_token_events(raw)
    assert list(tev) == [1]
    issue = {"number": 1, "created_at": "2026-11-09T10:00:00Z", "closed_at": None,
             "labels": [], "milestone": {"title": "v1"}}
    g = sd.group_series([issue], tev, DAYS, "milestone")
    assert g == {"(none)": [1, 0, 0], "v1": [0, 1, 1]}


def test_median_hours_and_hero():
    pairs = [("2026-11-10T00:00:00Z", "2026-11-10T01:00:00Z"),
             ("2026-11-10T00:00:00Z", "2026-11-10T03:00:00Z"),
             ("2026-11-01T00:00:00Z", "2026-11-12T00:00:00Z")]
    assert sd.median_hours(pairs) == 3.0
    assert sd.median_hours(pairs, since=sd.parse_ts("2026-11-10T02:00:00Z")) == 133.5
    assert sd.median_hours([]) is None
    h = sd.hero_stats(["2026-11-12T08:00:00Z", "2026-11-11T20:00:00Z", "2026-11-09T01:00:00Z"],
                      [], ["2026-11-12T01:00:00Z"], "2026-11-09T15:00:00Z", NOW)
    assert h["day_n"] == 4 and h["prs"] == {"today": 1, "h24": 2, "total": 3}
    assert h["commits"]["today"] == 1 and h["issues"]["total"] == 0


def test_percentile_hours_p90_nearest_rank():
    pairs = [("2026-11-10T00:00:00Z", "2026-11-10T%02d:00:00Z" % h) for h in range(1, 11)]
    assert sd.percentile_hours(pairs, 50) == 5.0
    assert sd.percentile_hours(pairs, 90) == 9.0
    assert sd.percentile_hours([], 90) is None


def test_render_html_vision_block_first_from_vision_md():
    series = sd.build_series(fake_api, "o/r", "dev", NOW)
    page = sd.render_html(series)
    body = page.split("<body>", 1)[1].lstrip()
    assert body.startswith('<section id="vision"')
    block = body.split("</section>", 1)[0]
    vision = (Path(__file__).resolve().parents[1] / "docs" / "VISION.md").read_text(encoding="utf-8")
    for node in ("MODE", "PLAT", "LANG"):
        assert 'data-node="%s"' % node in block
    for word in ("listen", "Windows", "zh"):
        assert word in block and word in vision
    assert block.count('data-nongoal="1"') == 3
    for txt in ("Send audio or text to a cloud service", "Remember a voiceprint without consent",
                "Send usage data home"):
        assert txt in vision and txt in block
