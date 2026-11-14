"""#772: release.yml builds the setup wizard from the release's prebuilt core and reports its size."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent


def _bi():
    spec = importlib.util.spec_from_file_location("build_installer_772", ROOT / "installer" / "build_installer.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _exe(tmp_path: Path, mb: float) -> Path:
    p = tmp_path / "tinglongwu-setup-9.9.9.exe"
    with open(p, "wb") as f:
        f.truncate(int(mb * (1 << 20)))
    return p


def test_size_line_within_and_over_budget(tmp_path):
    bi = _bi()
    ok, line = bi.wizard_size_line(_exe(tmp_path, 10), max_mb=bi.WIZARD_MAX_MB)
    assert ok and "10.0 MB" in line and "within" in line
    ok, line = bi.wizard_size_line(_exe(tmp_path, 12), max_mb=11)
    assert not ok and "OVER" in line


def test_size_report_warns_and_writes_summary(tmp_path, monkeypatch, capsys):
    bi = _bi()
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(bi, "WIZARD_MAX_MB", 1)
    exe = _exe(tmp_path, 2)
    assert bi.size_report(exe, max_mb=1) == 0          # a warning, never a failure
    out = capsys.readouterr().out
    assert "::warning::setup wizard" in out and "2.0 MB" in out
    assert "2.0 MB" in summary.read_text(encoding="utf-8")


def test_size_report_cli_and_missing_file(tmp_path, capsys):
    bi = _bi()
    exe = _exe(tmp_path, 1)
    assert bi.main(["--size-report", str(exe)]) == 0
    assert "::warning::" not in capsys.readouterr().out
    assert bi.main(["--size-report", str(tmp_path / "nope.exe")]) == 2


def _job():
    rel = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    return rel["jobs"]["windows-installer"]


def test_installer_job_uses_release_core_and_runs_on_dry_runs():
    job = _job()
    assert "windows" in job["needs"] and "release" in job["needs"]
    assert "needs.windows.result == 'success'" in job["if"]
    assert "needs.plan.outputs.publish != 'true'" in job["if"]     # dry run builds without a release
    steps = job["steps"]
    dl = [s for s in steps if "download-artifact" in s.get("uses", "")]
    assert dl and dl[0]["with"]["name"] == "release-win64"
    runs = "\n".join(s.get("run", "") for s in steps)
    assert "build_installer.py --core-dir" in runs
    assert "--size-report" in runs


def test_installer_upload_only_on_publish_and_names_kept():
    upload = [s for s in _job()["steps"] if s.get("name", "").startswith("Upload tinglongwu-setup")]
    assert len(upload) == 1
    assert upload[0]["if"] == "needs.plan.outputs.publish == 'true'"
    run = upload[0]["run"]
    assert 'exe="tinglongwu-setup-${TAG#v}.exe"' in run and "tinglongwu-setup.exe" in run
    assert "SHA256SUMS.txt" in run
