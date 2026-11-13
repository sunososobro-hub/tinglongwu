"""#71: install-test.yml runs on release, tests the release-asset one-liners, prints timing; README has the badge."""
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
ROOT = Path(__file__).resolve().parent.parent


def _wf():
    return yaml.safe_load((ROOT / ".github/workflows/install-test.yml").read_text(encoding="utf-8"))


def test_release_published_trigger():
    on = _wf().get("on", _wf().get(True))
    assert on["release"]["types"] == ["published"]
    assert "workflow_dispatch" in on


def test_release_assets_job_runs_readme_one_liners():
    job = _wf()["jobs"]["release-assets"]
    assert "release" in job["if"]
    text = "\n".join(s.get("run", "") for s in job["steps"])
    assert "releases/latest/download/$SCRIPT" in text
    assert "install took" in text


def test_every_install_job_prints_timing():
    for name in ("linux", "macos", "windows", "release-assets"):
        text = "\n".join(s.get("run", "") for s in _wf()["jobs"][name]["steps"])
        assert "install took" in text, name


def test_readme_has_install_test_badge():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "workflows/install-test.yml/badge.svg" in readme
