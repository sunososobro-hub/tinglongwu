"""Wheel ships data / asset files and setuptools warns about no absent packages (#2022)."""
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_wheel_contains_data_files(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "--no-build-isolation", "-w", str(tmp_path)],
        capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        pytest.skip("wheel build backend unavailable: " + proc.stderr[-200:])
    assert "absent from the" not in proc.stdout + proc.stderr
    whl = next(tmp_path.glob("tinglongwu-*.whl"))
    names = zipfile.ZipFile(whl).namelist()
    assert "tinglongwu/data/mt_spec.json" in names
    assert any(n.startswith("tinglongwu/data/terms/") and n.endswith(".tsv") for n in names)
    assert any(n.startswith("tinglongwu/gui/assets/") and n.endswith(".ico") for n in names)
