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
    assert "releases/latest/download" in text  # manual runs: the README URL
    assert "releases/download/$RELEASE_TAG" in text  # release events: the release that fired (latest/ skips prereleases)
    assert "| bash" not in text  # `curl | bash </dev/null` would feed bash EOF instead of the script
    assert 'bash "$RUNNER_TEMP/$SCRIPT" </dev/null' in text
    assert "install took" in text


def test_every_install_job_prints_timing():
    for name in ("linux", "macos", "windows", "release-assets"):
        text = "\n".join(s.get("run", "") for s in _wf()["jobs"][name]["steps"])
        assert "install took" in text, name


def test_readme_has_install_test_badge():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "workflows/install-test.yml/badge.svg" in readme


def test_manual_dispatch_can_pick_a_release_tag():
    wf = _wf()
    on = wf.get("on", wf.get(True))
    assert "release_tag" in on["workflow_dispatch"]["inputs"]
    env = wf["jobs"]["release-assets"]["steps"][1]["env"]
    assert "inputs.release_tag" in env["RELEASE_TAG"]


def test_release_assets_covers_windows_with_install_ps1():
    job = _wf()["jobs"]["release-assets"]
    assert "windows-latest" in job["strategy"]["matrix"]["os"]
    win = [s for s in job["steps"] if s.get("shell") == "powershell"]
    assert len(win) == 1
    run = win[0]["run"]
    assert "releases/download/$env:RELEASE_TAG" in run and "releases/latest/download" in run
    assert "install.ps1" in run and "install took" in run and "tlwu.exe" in run
    assert win[0]["env"]["TLWU_SKIP_MODELS"] == "1"
    # the bash steps must not run on the Windows cell
    for s in job["steps"]:
        if s.get("shell") != "powershell" and "run" in s and "Audio" not in s["name"]:
            assert "Windows" in s["if"], s["name"]


def test_release_workflow_dispatches_install_test_after_publish():
    rel = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    job = rel["jobs"]["windows-installer"]
    assert job["permissions"]["actions"] == "write"
    runs = [s.get("run", "") for s in job["steps"]]
    text = "\n".join(runs)
    assert "gh workflow run install-test.yml" in text
    assert "release_tag=" in text
    assert text.index("--draft=false") < text.index("gh workflow run install-test.yml")


def test_release_assets_skips_mobile_tags():
    cond = _wf()["jobs"]["release-assets"]["if"]
    assert "startsWith(github.event.release.tag_name, 'mobile-')" in cond
    assert "startsWith(inputs.release_tag, 'mobile-')" in cond
