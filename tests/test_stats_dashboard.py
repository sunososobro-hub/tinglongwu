import os
import json
import re
import sys
from datetime import datetime, UTC
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import stats_dashboard as sd  # noqa: E402

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
SECTIONS = ("output", "next", "install", "effort", "releases", "popularity")  # #1213 page order


def page_data(page):
    """The series JSON the page draws from (#1213: every chart is drawn client-side from it)."""
    m = re.search(r'<script id="d" type="application/json">(.*?)</script>', page, re.S)
    return json.loads(m.group(1))


def render_dom(page, tmp_path, width=1280):
    """Load the page in Chromium -> (body text, JS errors). Skips when Playwright/Chromium is missing."""
    pw = pytest.importorskip("playwright.sync_api")
    f = tmp_path / "page.html"
    f.write_text(page, encoding="utf-8")
    with pw.sync_playwright() as p:
        try:
            br = p.chromium.launch(executable_path=os.environ.get("TLWU_CHROMIUM") or None)
        except Exception as e:  # Chromium missing
            pytest.skip("chromium unavailable: %s" % e)
        try:
            pg = br.new_page(viewport={"width": width, "height": 900})
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f.as_uri())
            return pg.inner_text("body"), errors
        finally:
            br.close()


def fake_api(path, accept=None):
    if path == "/repos/o/r":
        return {"stargazers_count": 7, "forks_count": 2, "created_at": "2026-11-10T08:00:00Z"}
    if "/stargazers" in path:
        assert accept and "star" in accept
        return [{"starred_at": "2026-11-10T09:00:00Z"}, {"starred_at": "2026-11-12T01:00:00Z"}]
    if "/forks" in path:
        return [{"created_at": "2026-11-11T09:00:00Z"}]
    if "/releases" in path:
        return [{"tag_name": "v0.2.0", "assets": [{"download_count": 30}, {"download_count": 10}]},
                {"tag_name": "v0.1.0", "assets": [{"download_count": 5}]}]
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
    assert 'id="c_thru"' in page and "Open issues by" in page and page_data(page)["days"][0] == "2026-11-10"
    series = json.loads((tmp_path / "series.json").read_text())
    assert series["days"][0] == "2026-11-10" and series["stars"][-1] == 2
    assert series["forks"] == [0, 1, 1] and series["open_issues"][:3] == [2, 1, 1]
    assert series["groups"]["priority"]["P1"] == [0, 1, 1]  # P1 labeled on 11-11
    assert series["groups"]["milestone"]["v1"][-1] == 1
    assert series["prs_merged"] == [0, 0, 1] and series["issues_closed"] == [0, 1, 0]
    assert series["speed"]["pr_all"] == 24.0 and series["hero"]["prs"]["total"] == 1
    assert "Work finished per day" in page and page.index("Work finished") < page.index("Stars and forks")
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


def test_page_sections_in_order_without_vision_block_1213():
    page = sd.render_html(sd.build_series(fake_api, "o/r", "dev", NOW))
    pos = [page.index('<h2 id="%s">' % k) for k in SECTIONS]
    assert pos == sorted(pos)
    assert 'id="vision"' not in page and 'href="roadmap.html"' in page and 'href="docs.html"' in page
    assert "<title>TingLong dashboard</title>" in page


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
    assert {n["name"] for n in f["platforms"]} == {"windows", "linux", "all"}
    mix = {r["type"]: r for r in f["type_mix"]}
    assert abs(mix["bug"]["share"] - 100 * 20 / 60) < 1e-9 and mix["bug"]["target"] == 30
    assert abs(mix["bug"]["delta"] - (100 * 20 / 60 - 30)) < 1e-9
    assert mix["eval"]["share"] == 0.0 and mix["documentation"]["target"] == 5
    assert abs(sum(r["share"] for r in f["type_mix"]) - 100.0) < 1e-9


def test_flow_empty_and_mode_shares():
    assert sd.build_flow([])["total"] == 0
    shares = {n["name"]: round(n["share"]) for n in sd.build_flow(FLOW_ITEMS)["modes"]}
    assert shares["speak"] == 33


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
    data = page_data(page)
    assert data["faucet"] == {"level": 4, "focus": ["speak"]} and data["flow"]["total"] == 20.0
    assert "Where the effort goes" in page and "Type mix vs target" in page and 'id="faucet"' in page
    assert page.index("Work finished per day") < page.index("Where the effort goes")


def test_run_without_faucet_or_usage_does_not_fail(tmp_path):
    sd.run(str(tmp_path), "o/r", "dev", api=fake_api, now=NOW)  # fake_api rejects #574 -> degraded
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    data = page_data(page)
    assert data["faucet"] is None and data["flow"]["total"] == 0


def test_label_with_placeholder_is_not_substituted_again():
    flow = sd.build_flow([{"model": "sonnet", "minutes": 5,
                           "labels": ["mode:__DATA__<img src=x onerror=alert(1)>"]}])
    series = {"repo": "o/r", "branch": "dev", "flow": flow, "faucet": None, "days": [], "created": "2026-11-10"}
    page = sd.render_html(series)
    assert "<img" not in page and page.count("</script>") == 2  # the label cannot open a tag or end the data block
    assert any("<img" in n["name"] for n in page_data(page)["flow"]["modes"])  # ...and survives intact as data


def test_speed_block_shows_medians_only_for_three_windows(tmp_path):
    sd.run(str(tmp_path), "o/r", "dev", api=fake_api, now=NOW)
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    series = json.loads((tmp_path / "series.json").read_text())
    assert "p90" not in page and "p90" not in json.dumps(series)
    assert "speed_p90" not in series
    sp = series["speed"]
    # PR merged 11-12T08:00 (4 h before NOW), opened 24 h earlier -> in every window
    assert sp["pr_all"] == sp["pr_7d"] == sp["pr_24h"] == 24.0
    # issue closed 11-11T10:00 is 26 h before NOW -> in 7d/all-time, not in 24 h
    assert sp["issue_all"] == sp["issue_7d"] == 24.0 and sp["issue_24h"] is None


def test_every_card_has_a_title():
    page = sd.render_html(sd.build_series(fake_api, "o/r", "dev", NOW))
    cards = page.count('<div class="card"')
    assert cards >= 14 and len(re.findall(r'<div class="card"(?: style="[^"]*")?><h3>', page)) == cards


def test_activity_grid_multi_column_on_desktop_no_hscroll_on_phone(tmp_path):
    import pytest
    pw = pytest.importorskip("playwright.sync_api")
    series = sd.build_series(fake_api, "o/r", "dev", NOW)
    f = tmp_path / "index.html"
    f.write_text(sd.render_html(series), encoding="utf-8")
    with pw.sync_playwright() as p:
        try:
            br = p.chromium.launch(executable_path=os.environ.get("TLWU_CHROMIUM") or None)
        except Exception as e:  # Chromium missing
            pytest.skip("chromium unavailable: %s" % e)
        try:
            pg = br.new_page(viewport={"width": 1440, "height": 900})
            pg.goto(f.as_uri())
            tops = pg.eval_on_selector_all(".card", "els=>els.map(e=>Math.round(e.getBoundingClientRect().top))")
            assert len(tops) != len(set(tops)), tops  # at least two blocks share a top offset
            pg.set_viewport_size({"width": 390, "height": 844})
            assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        finally:
            br.close()


def test_downloads_snapshot_series_and_per_release(tmp_path):
    old = [{"ts": "2026-11-11T10:00:00Z", "stars": 5, "forks": 2, "open_issues": 1,
            "labels": {}, "commits_by_hour": {}, "downloads": 20}]
    (tmp_path / "data.json").write_text(json.dumps(old))
    hist = sd.run(str(tmp_path), "o/r", "dev", api=fake_api, now=NOW)
    assert hist[-1]["downloads"] == 45
    dl = json.loads((tmp_path / "series.json").read_text())["downloads"]
    assert dl["total"] == 45 and dl["releases"][0] == {"tag": "v0.2.0", "downloads": 40}
    assert dl["series"] == [None, 20, 45]  # no data before the first snapshot; live total today
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "Downloads" in page and 'id="c_dl"' in page and page_data(page)["downloads"]["total"] == 45


def test_downloads_unavailable_does_not_fail():
    def api(path, accept=None):
        if "/releases" in path:
            raise OSError("boom")
        return fake_api(path, accept)
    series = sd.build_series(api, "o/r", "dev", NOW)
    assert series["downloads"] == {"total": None, "releases": [], "series": [None, None, None]}
    assert sd.build_snapshot(api, "o/r", "dev", NOW)["downloads"] is None
    assert 'id="b_dl"' in sd.render_html(series)


def test_flow_platform_mobile_and_all_nodes():
    f = sd.build_flow([
        {"model": "sonnet", "minutes": 10, "labels": ["bug", "mode:listen", "area:mobile"]},
        {"model": "sonnet", "minutes": 10, "labels": ["bug", "mode:listen"]},
        {"model": "sonnet", "minutes": 10, "labels": ["bug", "mode:listen", "os:windows", "area:mobile"]},
        {"model": "sonnet", "minutes": 10, "labels": ["bug"]},
    ])
    plat = {n["name"]: n["quota"] for n in f["platforms"]}
    assert plat == {"mobile": 10.0, "all": 20.0, "windows": 10.0}
    assert {n["name"] for n in f["langs"]} == {"all"}
    assert {n["name"]: n["quota"] for n in f["modes"]} == {"listen": 30.0, "unlabelled": 10.0}


def test_run_fetches_releases_once(tmp_path):
    calls = []

    def api(path, accept=None):
        if "/releases" in path:
            calls.append(path)
        return fake_api(path, accept)
    sd.run(str(tmp_path), "o/r", "dev", api=api, now=NOW)
    assert len(calls) == 1


def test_downloads_unavailable_with_history_stays_empty():
    def api(path, accept=None):
        if "/releases" in path:
            raise OSError("boom")
        return fake_api(path, accept)
    hist = [{"ts": "2026-11-11T10:00:00Z", "downloads": 20}]
    series = sd.build_series(api, "o/r", "dev", NOW, hist)
    assert series["downloads"]["series"] == [None, None, None]


# -- #806 active installs from the anonymous update-check counter ------------------------------------------------
INSTALL_ROWS = [{"date": "2026-11-11", "version": "0.3.3", "os": "windows", "n": 40},
                {"date": "2026-11-11", "version": "0.3.4", "os": "windows", "n": 12},
                {"date": "2026-11-10", "version": "0.3.3", "os": "windows", "n": 30},
                {"date": "2026-11-12", "version": "0.3.4", "os": "windows", "n": 99},  # today: incomplete, left out
                {"date": "2026-01-01", "version": "0.1.0", "os": "linux", "n": 5}]  # too old


def test_active_installs_per_day_and_week_per_version():
    inst = sd.active_installs(INSTALL_ROWS, NOW)
    assert inst["days"][-1] == "2026-11-11" and len(inst["days"]) == sd.INSTALL_DAYS
    assert inst["total"][-2:] == [30, 52]
    assert inst["versions"] == [{"version": "0.3.3", "os": "windows", "last_day": 40, "week_avg": 10.0},
                                {"version": "0.3.4", "os": "windows", "last_day": 12, "week_avg": 1.7}]
    assert sd.active_installs(None, NOW) is None


def test_run_renders_active_installs(tmp_path):
    sd.run(str(tmp_path), "o/r", "dev", api=fake_api, now=NOW, installs=INSTALL_ROWS)
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "Active installs" in page and 'id="inst"' in page
    inst = page_data(page)["installs"]
    assert inst["total"][-1] == 52 and inst["versions"][0] == {"version": "0.3.3", "os": "windows", "last_day": 40, "week_avg": 10.0}
    sd.run(str(tmp_path), "o/r", "dev", api=fake_api, now=NOW, installs=None)
    assert page_data((tmp_path / "index.html").read_text(encoding="utf-8"))["installs"] is None


def test_counter_base_env_and_no_fetch_without_https():
    assert sd.counter_base({"TLWU_UPDATE_COUNTER": "https://w.example/"}) == "https://w.example"
    assert sd.fetch_installs("") is None and sd.fetch_installs("http://plain") is None


@pytest.mark.parametrize("bad", [{"error": "unavailable"}, ["x"], [{"date": "2026-11-11", "version": "0.3.3", "os": "windows", "n": "lots"}],
                                 [{"date": [], "version": "0.3.3", "os": "windows", "n": 1}],
                                 [{"date": {}, "version": "0.3.3", "os": "windows", "n": 1}],
                                 [{"date": "2026-11-11", "version": "0.3.3", "os": "windows", "n": [1]}]])
def test_run_survives_bad_counter_answer(tmp_path, bad):
    sd.run(str(tmp_path), "o/r", "dev", api=fake_api, now=NOW, installs=bad)
    assert page_data((tmp_path / "index.html").read_text(encoding="utf-8"))["installs"] is None


def test_infer_dims_path_rules_662():
    f = sd.infer_dims
    assert f(["mobile/lib/main.dart"]) == {"os": "mobile"}
    assert f(["installer/x.iss"])["os"] == "windows" and f(["tinglongwu/capture/wasapi_loop.py"])["os"] == "windows"
    assert f(["scripts/mac_install.sh"])["os"] == "mac" and f(["tinglongwu/capture/macos_tap.py"])["os"] == "mac"
    assert f(["install.sh"])["os"] == "linux" and f(["tinglongwu/capture/pulse.py"])["os"] == "linux"
    assert f(["tinglongwu/tts.py"])["mode"] == "speak" and f(["tinglongwu/reverse/x.py"])["mode"] == "speak"
    assert f(["tinglongwu/asr.py"])["mode"] == "listen" and f(["tinglongwu/segment.py"])["mode"] == "listen"
    assert f(["eval/flores_ja.tsv"])["lang"] == "ja"
    assert f(["README.md"]) == {}


def test_build_flow_uses_inference_only_when_label_missing_662():
    items = [{"model": "sonnet", "minutes": 10, "labels": [], "files": ["tinglongwu/tts.py", "installer/a.iss"]},
             {"model": "sonnet", "minutes": 10, "labels": ["mode:listen", "os:linux"], "files": ["tinglongwu/tts.py"]}]
    flow = sd.build_flow(items)
    assert {tuple(p[:3]) for p in flow["paths"]} == {("speak", "windows", "all"), ("listen", "linux", "all")}
    assert flow["inferred"]["mode"] == 50.0 and flow["inferred"]["os"] == 50.0 and flow["unclassified"] == 0.0


def test_shared_mode_is_infrastructure_split_by_area_662():
    items = [{"model": "sonnet", "minutes": 10, "labels": ["maint", "mode:shared", "area:ci"]},
             {"model": "sonnet", "minutes": 5, "labels": ["maint", "mode:shared", "area:install"]},
             {"model": "sonnet", "minutes": 5, "labels": ["maint", "mode:shared"]},
             {"model": "sonnet", "minutes": 20, "labels": ["bug", "mode:speak", "area:tts"]}]
    flow = sd.build_flow(items)
    modes = {n["name"]: n["quota"] for n in flow["modes"]}
    assert modes == {"infrastructure ci": 10.0, "infrastructure install": 5.0, "infrastructure": 5.0, "speak": 20.0}
    assert "shared" not in modes and flow["unclassified"] == 0.0
    assert {n["name"]: round(n["share"]) for n in flow["modes"]}["infrastructure ci"] == 25


def test_flow_backfill_covers_14_days_with_inference_662():
    now = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)

    def api(path, accept=None):
        if path.endswith("/files?per_page=100"):
            return [{"filename": "tinglongwu/tts.py"}]
        if path.endswith("/issues/574"):
            return {"body": "level: 2"}
        return {"labels": []}

    def pr(n, merged):
        return {"number": n, "merged_at": merged, "body": "Closes #%d\n\ntlwu-usage: model=sonnet tool_calls=1 minutes=10" % n}

    prs = [pr(1, "2026-11-14T10:00:00Z"), pr(2, "2026-11-05T10:00:00Z"), pr(3, "2026-10-20T10:00:00Z")]
    flow, _ = sd.build_flow_inputs(api, "o/r", prs, now)
    assert flow["total"] == 10.0  # 48 h window: only PR 1
    assert flow["backfill"]["days"] == 14 and flow["backfill"]["total"] == 20.0  # PRs 1 and 2, not 3
    assert flow["backfill"]["unclassified"] == 0.0 and flow["backfill"]["inferred"]["mode"] == 100.0


def test_flow_warm_files_cache_makes_no_files_or_issue_calls_662():
    now = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    calls = []

    def api(path, accept=None):
        calls.append(path)
        if path.endswith("/files?per_page=100"):
            return [{"filename": "tinglongwu/tts.py"}]
        if path.endswith("/issues/574"):
            return {"body": "level: 2"}
        return {"labels": []}

    prs = [{"number": n, "merged_at": "2026-11-1%dT10:00:00Z" % (n % 4),
            "body": "Closes #%d\n\ntlwu-usage: model=sonnet tool_calls=1 minutes=10" % (n + 100)} for n in range(1, 5)]
    labels = {n + 100: ["bug"] for n in range(1, 5)}
    cache = {}
    cold, _ = sd.build_flow_inputs(api, "o/r", prs, now, labels, cache)
    assert sum(p.endswith("/files?per_page=100") for p in calls) == 4
    assert not any("/issues/1" in p for p in calls)  # labels came from the issue list
    assert sorted(cache) == ["1", "2", "3", "4"]
    calls.clear()
    warm, _ = sd.build_flow_inputs(api, "o/r", prs, now, labels, cache)
    assert calls == ["/repos/o/r/issues/574"]  # second run: 0 files calls, 0 issue calls
    assert warm == cold


def test_run_persists_pr_files_cache_662(tmp_path):
    files_calls = []

    def api(path, accept=None):
        if path.endswith("/files?per_page=100"):
            files_calls.append(path)
            return [{"filename": "mobile/lib/main.dart"}]
        if path == "/repos/o/r/issues/574":
            return {"body": "level: 2"}
        if "/pulls?" in path:
            return [{"number": 41, "created_at": "2026-11-12T08:00:00Z", "merged_at": "2026-11-12T10:00:00Z",
                     "body": "Closes #9\ntlwu-usage: model=sonnet tool_calls=5 minutes=10"}]
        return fake_api(path, accept)

    sd.run(str(tmp_path), "o/r", "dev", api=api, now=NOW)
    assert json.loads((tmp_path / sd.PR_FILES).read_text()) == {"41": ["mobile/lib/main.dart"]}
    sd.run(str(tmp_path), "o/r", "dev", api=api, now=NOW)
    assert len(files_calls) == 1  # second run reads the cache file
    big = {str(n): [] for n in range(sd.PR_FILES_KEEP + 5)}
    pruned = sd.prune_files_cache(big)
    assert len(pruned) == sd.PR_FILES_KEEP and "0" not in pruned and str(sd.PR_FILES_KEEP + 4) in pruned


def test_rendered_page_has_no_js_errors_and_degrades_1213(tmp_path):
    """Degraded data (no faucet, no quota, no installs, no releases) still draws every section without errors."""
    page = sd.render_html(sd.build_series(fake_api, "o/r", "dev", NOW))
    for width in (1280, 390):
        text, errors = render_dom(page, tmp_path, width)
        assert errors == [], errors
    for msg in ("Faucet level unavailable", "No quota minutes in the last 48 h",
                "Install counter not available yet", "Work finished per day"):
        assert msg in text, msg

