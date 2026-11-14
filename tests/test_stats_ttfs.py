import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import stats_dashboard as sd  # noqa: E402


def doc(i, os_="windows", excl=100.0):
    return {"ttfs_seconds": excl + 5, "download_seconds": 5.0, "excl_download_seconds": excl,
            "setup_seconds": 50.0, "first_subtitle_seconds": 40.0, "os": os_,
            "version": "0.3.%d" % i, "sha": "abc%d" % i, "date": "2026-11-%02d" % (i + 1)}


def test_constants():
    assert sd.TTFS_POINTS == 10 and sd.TTFS_BUDGET_S == 180


def test_twelve_runs_show_last_ten_per_os():
    pts = sd.ttfs_points([doc(i) for i in range(12)] + [doc(1, "linux")])
    assert [r["version"] for r in pts["windows"]] == ["0.3.%d" % i for i in range(2, 12)]
    assert len(pts["linux"]) == 1


def test_malformed_docs_skipped():
    bad = doc(1)
    bad["excl_download_seconds"] = None
    assert sd.ttfs_points([bad, {"os": "x"}, "junk", None]) == {}


def test_render_table_and_over_budget_and_empty():
    page = sd.render_ttfs_section({"ttfs": sd.ttfs_points([doc(1, excl=100), doc(2, excl=200)])})
    assert 'data-os="windows"' in page and "100 s" in page and "200 s" in page and "180 s" in page
    assert page.count("#c0392b") == 1  # only the over-budget row is flagged
    assert "no data yet" in sd.render_ttfs_section({"ttfs": {}})
    assert "no data yet" in sd.render_ttfs_section({})


def test_collect_uses_artifacts_and_survives_failure():
    def api(path, accept=None):
        if "install-clean.yml/runs" in path:
            return {"workflow_runs": [{"id": 1}, {"id": 2}]}
        if path.endswith("/runs/1/artifacts"):
            return {"artifacts": [{"name": "ttfs-windows", "archive_download_url": "u1"},
                                  {"name": "other", "archive_download_url": "u2"}]}
        return {"artifacts": [{"name": "ttfs-linux", "archive_download_url": "u3", "expired": True}]}

    got = sd.collect_ttfs(api, "o/r", fetch=lambda u: doc(1))
    assert list(got) == ["windows"] and len(got["windows"]) == 1

    def boom(path, accept=None):
        raise OSError("down")
    assert sd.collect_ttfs(boom, "o/r") == {}
