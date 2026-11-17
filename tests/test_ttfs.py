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
    p, out = _run(tmp_path, PY + " -c \"import time;time.sleep(0.3);print('source line', flush=True);time.sleep(0.5);print('\\u2192 [EN] hello')\"")
    assert p.returncode == 0, p.stderr
    assert p.stdout.startswith("ttfs_seconds=")
    assert "excl_download_seconds=" in p.stdout
    d = json.loads(out.read_text())
    assert set(d) == {"ttfs_seconds", "download_seconds", "excl_download_seconds", "install_seconds",
                      "setup_seconds", "first_subtitle_seconds", "os", "version",
                      "sha", "date", "install_phases", "setup_phases"}
    assert d["first_subtitle_seconds"] >= 0.8  # waits past the source line for the translation
    assert d["ttfs_seconds"] >= d["first_subtitle_seconds"]
    assert abs(d["excl_download_seconds"] - (d["ttfs_seconds"] - d["download_seconds"])) < 0.01


def test_download_seconds_from_setup_output(tmp_path):
    # 125 MB at 50 Mbps = 20 s
    p, out = _run(tmp_path, PY + " -c \"print('\\u2192 x')\"",
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


def test_source_line_alone_does_not_count(tmp_path):
    p, out = _run(tmp_path, PY + " -c \"import time;print('source only', flush=True);time.sleep(30)\"",
                  extra=("--timeout", "1"))
    assert p.returncode == 2
    assert json.loads(out.read_text())["ttfs_seconds"] is None


# ---- #855: every file counts once, however many progress lines it printed

def _ttfs():
    spec = importlib.util.spec_from_file_location("ttfs", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _progress(key, total_mb, upto=100, step=5):
    """What tinglongwu_setup.downloader.copy_with_progress prints: `download <key>: N% (x/T MB)` every 5 %."""
    return [f"download {key}: {p}% ({total_mb * p // 100}/{total_mb} MB)" for p in range(step, upto + 1, step)]


def test_20_progress_lines_plus_announce_count_the_file_once():
    lines = ["downloading w (~250 MB)"] + _progress("w", 250)
    assert len(lines) == 21 and len(_progress("w", 250)) == 20
    got = _ttfs().parse_download_bytes("\n".join(lines))
    assert abs(got - 250_000_000) <= 2_500_000  # 250 MB +-1 %, not ~20x
    assert abs(_ttfs().download_seconds_at_baseline(got) - 40.0) < 0.5  # 250 MB at 50 Mbps, not 400 s


def test_two_files_each_counted_once():
    text = "\n".join(_progress("en-zh", 100) + _progress("zh-en", 60))
    assert _ttfs().parse_download_bytes(text) == 160_000_000


def test_a_download_that_stopped_early_counts_the_bytes_seen():
    text = "downloading w (~250 MB)\n" + "\n".join(_progress("w", 250, upto=60))
    assert _ttfs().parse_download_bytes(text) == 150_000_000  # 60 % of 250 MB, not the announced size


def test_unknown_size_progress_counts_the_last_value_once():
    text = "download k: 50 MB\ndownload k: 100 MB\ndownload k: 150 MB"
    assert _ttfs().parse_download_bytes(text) == 150_000_000


def test_announce_without_progress_counts_once_even_if_printed_twice():
    text = "downloading a.onnx (~80 MB)\ndownloading a.onnx (~80 MB)\ndownloading b.bin (~20 MB)"
    assert _ttfs().parse_download_bytes(text) == 100_000_000


def test_other_download_lines_keep_counting(tmp_path):
    assert _ttfs().parse_download_bytes("Downloading model: 125 MB") == 125_000_000
    text = "Downloading model: 125 MB\n" + "\n".join(_progress("w", 250))
    assert _ttfs().parse_download_bytes(text) == 375_000_000


def test_setup_output_with_progress_lines_gives_download_seconds_of_one_copy(tmp_path):
    code = ("print('downloading w (~250 MB)')\n"
            "for p in range(5, 101, 5): print(f'download w: {p}% ({250 * p // 100}/250 MB)')\n")
    p, out = _run(tmp_path, PY + " -c \"print('\\u2192 x')\"", setup="%s -c \"%s\"" % (PY, code.replace('"', '\\"')))
    assert p.returncode == 0, p.stderr
    assert abs(json.loads(out.read_text())["download_seconds"] - 40.0) < 0.5


def test_over_budget_run_still_exits_0(tmp_path, monkeypatch, capsys):
    """Run main() (the timing path) with fake stages that add up to more than TTFS_BUDGET_S."""
    m = _ttfs()
    stages = iter([(100.0, 0, ""), (120.0, 0, "\n".join(_progress("w", 250)))])  # install 100 s, setup 120 s
    monkeypatch.setattr(m, "_run", lambda cmd, stamps=None: next(stages))
    monkeypatch.setattr(m, "time_first_subtitle", lambda cmd, timeout=0, out_lines=None: 5.0)
    out = tmp_path / "ttfs.json"
    assert m.main(["--install", "x", "--setup", "y", "--tlwu", "z", "--out", str(out)]) == 0
    d = json.loads(out.read_text())
    assert d["ttfs_seconds"] == 225.0 and d["ttfs_seconds"] > m.TTFS_BUDGET_S  # over budget, still a measurement
    assert abs(d["download_seconds"] - 40.0) < 0.5 and d["excl_download_seconds"] > 0  # not negative any more
    assert "ttfs_seconds=225.0" in capsys.readouterr().out


def test_no_subtitle_reports_exit_codes_and_output_tail_1060(tmp_path):
    # #1060: when no subtitle arrives, stderr shows each step's exit code and the subtitle command's
    # stdout+stderr tail, so a red CI ttfs job says why
    tlwu = PY + " -c \"import sys;[print('line', i) for i in range(100)];sys.stdout.flush();sys.stderr.write('Boom: model missing\\n')\""
    out = tmp_path / "ttfs.json"
    p = subprocess.run(
        [sys.executable, SCRIPT, "--install", PY + " -c \"print('pip failed');raise SystemExit(3)\"",
         "--setup", "echo setup", "--tlwu", tlwu, "--out", str(out), "--timeout", "20"],
        capture_output=True, text=True)
    assert p.returncode == 2
    assert "--- install (exit 3)" in p.stderr
    assert "pip failed" in p.stderr
    assert "--- setup (exit 0)" in p.stderr
    assert "Boom: model missing" in p.stderr   # stderr of the subtitle command is no longer dropped
    assert "line 99" in p.stderr and "line 50" not in p.stderr   # only the last 40 lines
    # stdout is flushed before the error, as a crashing tlwu does at exit; the tail keeps that order
    assert json.loads(out.read_text())["ttfs_seconds"] is None


def test_phase_seconds_and_slow_lines_1341():
    # #1341: a step runs from its `==> ` line to the next one; the slowest line is the one followed by the longest wait
    m = _ttfs()
    stamps = [(0.0, ""), (0.1, "==> 4/8 clone"), (2.0, "Collecting x"), (5.1, ""), (5.1, "==> 6/8 models"),
              (6.0, "Building wheel for llama-cpp-python (pyproject.toml): started"),
              (206.0, "Building wheel for llama-cpp-python (pyproject.toml): finished"), (207.0, "==> 8/8 self-test")]
    assert m.phase_seconds(stamps, 230.0) == [("4/8 clone", 5.0), ("6/8 models", 201.9), ("8/8 self-test", 23.0)]
    slow = m.slow_lines(stamps, 230.0)
    assert slow[0] == (200.0, "Building wheel for llama-cpp-python (pyproject.toml): started")
    assert slow[1] == (23.0, "==> 8/8 self-test") and all(s >= m.SLOW_LINE_MIN_S for s, _ in slow)
    assert m.phase_seconds([(0.5, "no steps")], 3.0) == [("(start)", 3.0)]
    rep = m.phase_report(stamps, 230.0)
    assert rep["phases"][1] == {"name": "6/8 models", "seconds": 201.9} and rep["slow_lines"][0]["seconds"] == 200.0


def test_run_stamps_each_line_and_ttfs_json_has_phases_1341(tmp_path):
    # the real path: the setup command prints two installer steps 0.6 s apart; ttfs.json carries them with seconds
    code = "import time;print('==> a');time.sleep(0.6);print('==> b');time.sleep(1.2);print('end')"
    p, out = _run(tmp_path, PY + " -c \"print('\\u2192 x')\"", setup='%s -c "%s"' % (PY, code))
    assert p.returncode == 0, p.stderr
    d = json.loads(out.read_text())
    ph = {r["name"]: r["seconds"] for r in d["setup_phases"]["phases"]}
    assert 0.5 <= ph["a"] < 1.2 and ph["b"] >= 1.1
    assert d["setup_phases"]["slow_lines"][0]["line"] == "==> b"
    assert d["install_phases"]["slow_lines"] == []


def test_log_option_writes_timed_installer_output_1341(tmp_path):
    log = tmp_path / "out.log"
    p, out = _run(tmp_path, PY + " -c \"print('\\u2192 x')\"", setup="echo models", extra=("--log", str(log)))
    assert p.returncode == 0, p.stderr
    lines = log.read_text().splitlines()
    assert lines[0] == "--- install" and lines[2] == "--- setup"
    sec, text = lines[1].split(None, 1)
    assert text == "install" and float(sec) >= 0 and lines[3].endswith("  models")
