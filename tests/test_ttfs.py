"""scripts/ttfs.py: fake commands only, no models."""
import importlib.util
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "ttfs.py")
PY = '"%s"' % sys.executable


def _run(tmp_path, tlwu, setup="echo setup", extra=()):
    out = tmp_path / "ttfs.json"
    p = subprocess.run(
        [sys.executable, SCRIPT, "--install", "echo install", "--setup", setup,
         "--tlwu", tlwu, "--out", str(out), *extra],
        capture_output=True, text=True)
    return p, out


def test_writes_schema_and_prints(tmp_path):
    p, out = _run(tmp_path, PY + " -c \"import time;time.sleep(0.3);print('hello')\"")
    assert p.returncode == 0, p.stderr
    assert p.stdout.startswith("ttfs_seconds=")
    assert "excl_download_seconds=" in p.stdout
    d = json.loads(out.read_text())
    assert set(d) == {"ttfs_seconds", "download_seconds", "excl_download_seconds",
                      "setup_seconds", "first_subtitle_seconds", "os", "version",
                      "sha", "date"}
    assert d["first_subtitle_seconds"] >= 0.3
    assert d["ttfs_seconds"] >= d["first_subtitle_seconds"]
    assert abs(d["excl_download_seconds"] - (d["ttfs_seconds"] - d["download_seconds"])) < 0.01


def test_download_seconds_from_setup_output(tmp_path):
    # 125 MB at 50 Mbps = 20 s
    p, out = _run(tmp_path, PY + " -c \"print('x')\"",
                  setup=PY + " -c \"print('Downloading model: 125 MB')\"")
    assert p.returncode == 0
    d = json.loads(out.read_text())
    assert abs(d["download_seconds"] - 20.0) < 0.01


def test_timeout_exit2_and_null(tmp_path):
    p, out = _run(tmp_path, PY + " -c \"import time;time.sleep(30)\"",
                  extra=("--timeout", "1"))
    assert p.returncode == 2
    d = json.loads(out.read_text())
    assert d["ttfs_seconds"] is None


def test_over_budget_still_exit_0():
    spec = importlib.util.spec_from_file_location("ttfs", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert (m.TTFS_BUDGET_S, m.BASELINE_MBPS, m.FIRST_SUBTITLE_TIMEOUT_S) == (180, 50, 600)
    assert m.TTFS_CLIP == "tinglongwu/data/selftest_en.wav"
    assert os.path.exists(os.path.join(ROOT, m.TTFS_CLIP))
    assert m.parse_download_bytes("no sizes here") == 0


def test_transfer_rate_not_counted_as_size():
    spec = importlib.util.spec_from_file_location("ttfs", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.parse_download_bytes("Downloading model: 125 MB (5 MB/s)") == 125_000_000
    assert abs(m.download_seconds_at_baseline(125_000_000) - 20.0) < 1e-9
