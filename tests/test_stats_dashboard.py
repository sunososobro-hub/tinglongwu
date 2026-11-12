import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import stats_dashboard as sd  # noqa: E402

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def fake_api(path):
    if path == "/repos/o/r":
        return {"stargazers_count": 7, "forks_count": 2}
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
    assert "<svg" in page and "commits 72h" in page
    assert json.loads((tmp_path / "data.json").read_text()) == hist


def test_same_hour_replaces_snapshot(tmp_path):
    sd.run(str(tmp_path), "o/r", "dev", api=fake_api, now=NOW)
    hist = sd.run(str(tmp_path), "o/r", "dev", api=fake_api, now=NOW)
    assert len(hist) == 1
