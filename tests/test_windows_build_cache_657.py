"""#657: windows-build keeps one warm Nuitka cache on dev that PR builds restore.

Before: `cancel-in-progress: true` cancelled almost every dev push run (so dev never saved a fresh cache), and every PR
run saved its own entry, which evicted dev's from the 10 GB repo cache. PR builds then compiled from zero (~40-50 min).
"""
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
ROOT = Path(__file__).resolve().parent.parent


def _wf():
    return yaml.safe_load((ROOT / ".github/workflows/windows-build.yml").read_text(encoding="utf-8"))


def _steps():
    return _wf()["jobs"]["build"]["steps"]


def _nuitka_steps():
    return [s for s in _steps() if "actions/cache" in str(s.get("uses", "")) and "NUITKA_CACHE_DIR" in str(s.get("with", {}).get("path", ""))]


def test_dev_push_runs_are_not_cancelled_only_pr_runs_are():
    cancel = str(_wf()["concurrency"]["cancel-in-progress"])
    assert cancel != "True"  # a plain `true` cancels the dev run that would save the cache
    assert "github.event_name == 'pull_request'" in cancel


def test_pr_runs_restore_but_never_save_the_nuitka_cache():
    steps = _nuitka_steps()
    restores = [s for s in steps if "actions/cache/restore@" in s["uses"]]
    saves = [s for s in steps if "actions/cache/save@" in s["uses"]]
    assert len(restores) == 1 and len(saves) == 1
    assert not [s for s in steps if s not in restores + saves]  # no combined actions/cache (it saves on PRs too)
    assert "github.event_name != 'pull_request'" in saves[0]["if"]
    assert "steps.changes.outputs.build == 'true'" in saves[0]["if"]
    assert saves[0]["with"]["key"] == restores[0]["with"]["key"]


def test_cache_is_saved_right_after_a_successful_build_before_the_smoke_tests():
    steps = _steps()
    runs = [str(s.get("run", "")) for s in steps]
    build = next(i for i, r in enumerate(runs) if "build_windows.py" in r)
    save = next(i for i, s in enumerate(steps) if "actions/cache/save@" in str(s.get("uses", "")))
    assert save == build + 1
    assert "always()" not in steps[save]["if"]  # a failed build saves nothing
