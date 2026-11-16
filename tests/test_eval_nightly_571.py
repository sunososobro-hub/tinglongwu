"""#571: scripts/eval_nightly.py turns a smoke report + baseline into the nightly chrF-drop alert (run as the workflow does)."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "eval_nightly.py"


def write(tmp_path, en_zh_chrf, zh_en_chrf=48.0):
    base = {"model": "small", "engine": "hymt2-cpu", "clips": 5, "pairs": {
        "en->zh": {"chrf": 38.9, "chrf_margin": 2.0, "mt_median_s_max": 3.0},
        "zh->en": {"chrf": 43.4, "chrf_margin": 3.0, "mt_median_s_max": 3.0}}}
    rep = {"languages": [{"pair": "en->zh", "clips": 5, "chrf": en_zh_chrf, "mt_median_s": 1.0},
                         {"pair": "zh->en", "clips": 5, "chrf": zh_en_chrf, "mt_median_s": 1.0}]}
    (tmp_path / "b.json").write_text(json.dumps(base))
    (tmp_path / "r.json").write_text(json.dumps(rep))


def run(tmp_path, *extra):
    return subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "r.json"), "--baseline", str(tmp_path / "b.json"),
                           "--out", str(tmp_path / "alert.md"), "--date", "2026-11-15", *extra],
                          capture_output=True, text=True, cwd=tmp_path)


def test_drop_lists_pair_chrf_baseline_delta(tmp_path):
    write(tmp_path, 35.5)  # 38.9 - 2.0 = 36.9 > 35.5
    r = run(tmp_path)
    assert r.returncode == 0 and "drop=true" in r.stdout
    text = (tmp_path / "alert.md").read_text()
    assert "| en->zh | 35.50 | 38.90 | -3.40 | 2.00 |" in text
    assert "zh->en" not in text


def test_within_margin_writes_nothing(tmp_path):
    write(tmp_path, 37.0)
    r = run(tmp_path)
    assert r.returncode == 0 and "drop=false" in r.stdout
    assert not (tmp_path / "alert.md").exists()


def test_github_output_and_bad_report(tmp_path):
    write(tmp_path, 30.0)
    out = tmp_path / "gh_out"
    subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "r.json"), "--baseline", str(tmp_path / "b.json"),
                        "--out", str(tmp_path / "alert.md")], capture_output=True, text=True,
                       env={"GITHUB_OUTPUT": str(out), "PATH": ""})
    assert out.read_text().split() == ["drop=true", "test_run=false"]
    (tmp_path / "r.json").write_text("not json")
    assert run(tmp_path).returncode == 2


def test_raise_baseline_forces_marked_alert(tmp_path):
    write(tmp_path, 38.0)  # healthy: 38.0 > 36.9
    assert "drop=false" in run(tmp_path).stdout
    r = run(tmp_path, "--raise-baseline", "10")  # 48.9 - 2.0 = 46.9 > 38.0; zh->en 53.4 - 3.0 = 50.4 > 48.0
    assert r.returncode == 0 and "drop=true" in r.stdout
    text = (tmp_path / "alert.md").read_text()
    assert "| en->zh | 38.00 | 48.90 | -10.90 | 2.00 |" in text
    assert "| zh->en | 48.00 | 53.40 | -5.40 | 3.00 |" in text
    assert "Test run: baseline chrF raised by 10.00" in text


def test_raise_baseline_empty_is_zero_and_bad_values_fail(tmp_path):
    write(tmp_path, 38.0)
    r = run(tmp_path, "--raise-baseline", "")  # a scheduled run has no dispatch input
    assert r.returncode == 0 and "drop=false" in r.stdout
    for bad in ("abc", "-1", "101", "nan"):
        assert run(tmp_path, "--raise-baseline", bad).returncode == 2, bad


def test_raise_baseline_github_output_marks_test_run(tmp_path):
    write(tmp_path, 38.0)
    out = tmp_path / "gh_out"
    subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "r.json"), "--baseline", str(tmp_path / "b.json"),
                    "--out", str(tmp_path / "alert.md"), "--raise-baseline", "10"], capture_output=True, text=True,
                   env={"GITHUB_OUTPUT": str(out), "PATH": ""})
    assert out.read_text().split() == ["drop=true", "test_run=true"]
