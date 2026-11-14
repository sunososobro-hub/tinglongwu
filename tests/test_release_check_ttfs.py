"""release_check.py --ttfs prints the time to first subtitle and warns, never blocks (#776)."""
import json
import subprocess
import sys
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release_check.py"


def _run(tmp_path, ttfs_arg):
    z = tmp_path / "core.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("app/readme.txt", "x")
    cmd = [sys.executable, str(SCRIPT), str(z)] + ttfs_arg
    return subprocess.run(cmd, capture_output=True, text=True)


def _json(tmp_path, excl, ttfs=200.0):
    p = tmp_path / "ttfs.json"
    p.write_text(json.dumps({
        "ttfs_seconds": ttfs, "download_seconds": 20.0, "excl_download_seconds": excl,
        "setup_seconds": 50.0, "first_subtitle_seconds": 30.0,
        "os": "windows", "version": "0.3.4", "sha": "abc", "date": "2026-11-14"}))
    return p


def test_under_budget_no_warn(tmp_path):
    r = _run(tmp_path, ["--ttfs", str(_json(tmp_path, 120.0))])
    assert r.returncode == 0
    assert "ttfs: 120 s excl. download (windows, 0.3.4)" in r.stdout
    assert "WARN" not in r.stdout


def test_over_budget_warns_exit_unchanged(tmp_path):
    r = _run(tmp_path, ["--ttfs", str(_json(tmp_path, 181.0))])
    assert r.returncode == 0
    assert "WARN ttfs" in r.stdout


def test_missing_file_unknown(tmp_path):
    r = _run(tmp_path, ["--ttfs", str(tmp_path / "nope.json")])
    assert r.returncode == 0
    assert "ttfs: unknown (" in r.stdout


def test_null_unknown(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"ttfs_seconds": None}))
    r = _run(tmp_path, ["--ttfs", str(p)])
    assert r.returncode == 0
    assert "ttfs: unknown (" in r.stdout


def test_no_flag_prints_no_ttfs(tmp_path):
    r = _run(tmp_path, [])
    assert "ttfs" not in r.stdout


def test_nan_unknown(tmp_path):
    p = tmp_path / "t.json"
    p.write_text('{"ttfs_seconds": 200, "excl_download_seconds": NaN}')
    r = _run(tmp_path, ["--ttfs", str(p)])
    assert r.returncode == 0
    assert "ttfs: unknown (" in r.stdout
    assert "WARN" not in r.stdout


def test_huge_int_unknown(tmp_path):
    p = tmp_path / "t.json"
    p.write_text('{"ttfs_seconds": 200, "excl_download_seconds": 1' + "0" * 400 + "}")
    r = _run(tmp_path, ["--ttfs", str(p)])
    assert r.returncode == 0
    assert "ttfs: unknown (" in r.stdout
    assert "Traceback" not in r.stderr
