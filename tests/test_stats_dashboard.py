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


def test_mode_and_lang_dimensions_585():
    assert sd.group_of("mode:speak") == ("mode", "speak")
    assert sd.group_of("lang:ja") == ("lang", "ja")
    assert "mode" in sd.GROUPS and "lang" in sd.GROUPS
    issue = {"number": 7, "created_at": "2026-11-09T00:00:00Z", "closed_at": None,
             "labels": [{"name": "mode:two-way"}, {"name": "lang:zh"}, {"name": "lang:ru"}]}
    assert sd.group_series([issue], {}, DAYS, "mode") == {"two-way": [1, 1, 1]}
    assert sd.group_series([issue], {}, DAYS, "lang") == {"zh": [1, 1, 1], "ru": [1, 1, 1]}
    plain = dict(issue, number=8, labels=[{"name": "bug"}])
    assert sd.group_series([plain], {}, DAYS, "lang") == {"(none)": [1, 1, 1]}


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


FLOW_ITEMS = [
    {"model": "opus", "minutes": 10, "labels": ["bug", "mode:speak", "os:windows", "lang:en"]},
    {"model": "sonnet", "minutes": 20, "labels": ["enhancement", "mode:listen", "os:linux", "lang:zh"]},
    {"model": "sonnet", "minutes": 20, "labels": ["documentation"]},  # no dimension labels
]


def test_parse_usage_and_faucet_574():
    assert sd.parse_usage("x\ntlwu-usage: model=Opus tool_calls=3 minutes=12.5") == ("opus", 12.5)
    assert sd.parse_usage("no usage line") is None
    assert sd.parse_faucet("level: 3\nfocus: speak\nlegend...") == {"level": 3, "focus": ["speak"]}
    assert sd.parse_faucet("level: 2") == {"level": 2, "focus": []}
    assert sd.parse_faucet("garbage") is None and sd.parse_faucet(None) is None


def test_flow_totals_weight_opus_2x_and_unlabelled_node():
    f = sd.build_flow(FLOW_ITEMS)
    assert f["total"] == 60.0  # 10 x 2 (opus) + 20 + 20
    assert sum(n["share"] for n in f["modes"]) == 100.0 or abs(sum(n["share"] for n in f["modes"]) - 100.0) < 1e-9
    assert abs(sum(n["share"] for n in f["platforms"]) - 100.0) < 1e-9
    assert abs(sum(n["share"] for n in f["langs"]) - 100.0) < 1e-9
    modes = {n["name"]: n["quota"] for n in f["modes"]}
    assert modes == {"speak": 20.0, "listen": 20.0, "unlabelled": 20.0}
    assert {n["name"] for n in f["platforms"]} == {"windows", "linux", "unlabelled"}
    mix = {r["type"]: r for r in f["type_mix"]}
    assert abs(mix["bug"]["share"] - 100 * 20 / 60) < 1e-9 and mix["bug"]["target"] == 30
    assert abs(mix["bug"]["delta"] - (100 * 20 / 60 - 30)) < 1e-9
    assert mix["eval"]["share"] == 0.0 and mix["documentation"]["target"] == 5
    assert abs(sum(r["share"] for r in f["type_mix"]) - 100.0) < 1e-9


def test_flow_empty_and_render_inline_svg_with_focus_orange():
    assert sd.build_flow([])["total"] == 0
    assert "No quota" in sd.render_sankey(sd.build_flow([]))
    svg = sd.render_sankey(sd.build_flow(FLOW_ITEMS), focus=["speak"])
    assert svg.startswith("<svg") and "<path" in svg and "speak 33%" in svg
    assert sd.FOCUS_ORANGE in svg
    assert sd.FOCUS_ORANGE not in sd.render_sankey(sd.build_flow(FLOW_ITEMS), focus=[])


def test_run_renders_flow_type_mix_and_faucet(tmp_path):
    def api(path, accept=None):
        if path == "/repos/o/r/issues/574":
            return {"body": "level: 4\nfocus: speak\n"}
        if path == "/repos/o/r/issues/9":
            return {"labels": [{"name": "bug"}, {"name": "mode:speak"}, {"name": "os:windows"}, {"name": "lang:en"}]}
        if "/pulls?" in path:
            return [{"created_at": "2026-11-12T08:00:00Z", "merged_at": "2026-11-12T10:00:00Z",
                     "body": "Closes #9\ntlwu-usage: model=opus tool_calls=5 minutes=10"},
                    {"created_at": "2026-11-01T08:00:00Z", "merged_at": "2026-11-01T10:00:00Z",  # outside 48 h
                     "body": "Closes #9\ntlwu-usage: model=opus tool_calls=5 minutes=99"}]
        return fake_api(path, accept)
    sd.run(str(tmp_path), "o/r", "dev", api=api, now=NOW)
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    series = json.loads((tmp_path / "series.json").read_text())
    assert series["flow"]["total"] == 20.0 and series["faucet"] == {"level": 4, "focus": ["speak"]}
    assert "Where the quota goes" in page and "Type mix vs target" in page
    assert "Faucet level <b>4</b>" in page and "% of quota" in page
    assert page.index("Where the quota goes") < page.index("Work finished per day")


def test_run_without_faucet_or_usage_does_not_fail(tmp_path):
    sd.run(str(tmp_path), "o/r", "dev", api=fake_api, now=NOW)  # fake_api rejects #574 -> degraded
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "Faucet level unavailable" in page and "No quota minutes" in page
