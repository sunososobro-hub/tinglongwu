"""#775: scripts/ttfs_ci.py drives ttfs.py for the install-clean ttfs job and writes the job summary."""
import importlib.util
import json
import os
import sys

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)


def _load():
    spec = importlib.util.spec_from_file_location("ttfs_ci", os.path.join(SCRIPTS, "ttfs_ci.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_commands_skip_models_only_in_install_step():
    m = _load()
    install, setup, tlwu = m.commands("linux", "/tmp/my dir")
    assert "TLWU_SKIP_MODELS=1" in install and "TLWU_SKIP_MODELS" not in setup
    assert "scripts/install.sh" in install and "scripts/install.sh" in setup
    assert "'/tmp/my dir'" in install and tlwu.startswith("'/tmp/my dir'/.venv/bin/tlwu --file")
    mi, _, _ = m.commands("macos", "/x")
    assert "scripts/mac_install.sh" in mi and "TLWU_SKIP_BLACKHOLE=1" in mi


def test_summary_and_exit_code(tmp_path, monkeypatch):
    m = _load()
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        out = argv[argv.index("--out") + 1]
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"ttfs_seconds": 250.0, "excl_download_seconds": 170.5}, f)
        return 0

    monkeypatch.setattr(m.ttfs, "main", fake_main)
    summ = tmp_path / "s.md"
    rc = m.main(["--os", "linux", "--dir", "/d", "--out", str(tmp_path / "ttfs.json"), "--summary", str(summ)])
    assert rc == 0
    text = summ.read_text(encoding="utf-8")
    assert "ttfs_seconds: 250.0" in text and "excl_download_seconds: 170.5" in text
    assert "--install" in seen["argv"] and "--setup" in seen["argv"]


def test_no_subtitle_is_a_failure_with_na_summary(tmp_path, monkeypatch):
    m = _load()

    def fake_main(argv):
        with open(argv[argv.index("--out") + 1], "w", encoding="utf-8") as f:
            json.dump({"ttfs_seconds": None, "excl_download_seconds": None}, f)
        return 2

    monkeypatch.setattr(m.ttfs, "main", fake_main)
    summ = tmp_path / "s.md"
    assert m.main(["--os", "macos", "--dir", "/d", "--out", str(tmp_path / "o.json"), "--summary", str(summ)]) == 2
    assert "ttfs_seconds: n/a" in summ.read_text(encoding="utf-8")


def test_windows_commands_934():
    """#934: windows runs the released wizard silently, prints its install.log as the setup step, runs the venv tlwu."""
    m = _load()
    install, setup, tlwu = m.commands("windows", r"C:\t\tlwu", r"C:\dl\tinglongwu-setup.exe")
    assert install.startswith(r'start /wait "" "C:\dl\tinglongwu-setup.exe" /VERYSILENT')
    assert r'/DIR="C:\t\tlwu"' in install and "TLWU_SKIP_MODELS" not in install
    assert setup.startswith(r'type "C:\t\tlwu\install.log" & ')
    args = r'--file "C:\t\tlwu\tinglongwu\data\selftest_en.wav" --device cpu'
    # #1060: the wizard's core layout has {app}\tlwu.exe (no .venv): try it first, the venv exe only as fallback
    assert tlwu == (r'if exist "C:\t\tlwu\tlwu.exe" ("C:\t\tlwu\tlwu.exe" ' + args + r') else '
                    r'("C:\t\tlwu\.venv\Scripts\tlwu.exe" ' + args + ')')


def test_windows_needs_setup_exe_and_writes_download_mb(tmp_path, monkeypatch):
    import pytest
    m = _load()
    with pytest.raises(SystemExit):
        m.main(["--os", "windows", "--dir", "C:\d", "--out", str(tmp_path / "o.json")])

    def fake_main(argv):
        with open(argv[argv.index("--out") + 1], "w", encoding="utf-8") as f:
            json.dump({"ttfs_seconds": 300.0, "excl_download_seconds": 100.0, "download_seconds": 200.0}, f)
        return 0

    monkeypatch.setattr(m.ttfs, "main", fake_main)
    out = tmp_path / "ttfs.json"
    assert m.main(["--os", "windows", "--dir", "C:\d", "--setup-exe", "x.exe", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["download_mb"] == 1250.0


def test_setup_time_report_773(tmp_path):
    """#773: the wizard run is judged from install.log; setup_time.json has the #752 fields."""
    m = _load()
    (tmp_path / "install.log").write_text("READY default-listen seconds=30.5 download_mb=1200\n", encoding="utf-8")
    out = tmp_path / "setup_time.json"
    text = m.setup_time_report(str(tmp_path), 70.0, str(out))
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc == {"setup_seconds": 70.0, "ready": True, "download_seconds": 30.5, "download_mb": 1200, "pip_lines": 0}
    assert "OK: setup minus download 39.5s" in text


def test_windows_main_writes_setup_time_773(tmp_path, monkeypatch):
    m = _load()
    (tmp_path / "install.log").write_text("pip install foo", encoding="utf-8")

    def fake_main(argv):
        with open(argv[argv.index("--out") + 1], "w", encoding="utf-8") as f:
            json.dump({"install_seconds": 50.0, "download_seconds": 10.0}, f)
        return 0

    monkeypatch.setattr(m.ttfs, "main", fake_main)
    assert m.main(["--os", "windows", "--dir", str(tmp_path), "--setup-exe", "x.exe",
                   "--out", str(tmp_path / "ttfs.json")]) == 0
    doc = json.loads((tmp_path / "setup_time.json").read_text(encoding="utf-8"))
    assert doc["setup_seconds"] == 50.0 and doc["pip_lines"] == 1 and doc["ready"] is False


def test_install_size_in_summary_773(tmp_path):
    m = _load()
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.bin").write_bytes(b"x" * (1024 * 1024))
    (tmp_path / "sub" / "b.bin").write_bytes(b"x" * (1024 * 1024))
    assert m.dir_size_mb(str(tmp_path)) == 2.0
    assert m.dir_size_mb(str(tmp_path / "missing")) == 0.0
    assert "install_size_mb: 2.0" in m.summary_text("windows", {}, 2.0)
    assert "install_size_mb" not in m.summary_text("windows", {})


def test_summary_lists_phases_and_slowest_lines_1341():
    m = _load()
    doc = {"install_seconds": 28.0, "setup_seconds": 280.0, "first_subtitle_seconds": 9.5,
           "install_phases": {"phases": [{"name": "4/8 clone", "seconds": 21.0}], "slow_lines": []},
           "setup_phases": {"phases": [{"name": "6/8 models", "seconds": 240.0}],
                            "slow_lines": [{"seconds": 200.0, "line": "Building wheel for llama-cpp-python"}]}}
    text = m.summary_text("macos", doc)
    assert "install_seconds: 28.0  setup_seconds: 280.0  first_subtitle_seconds: 9.5" in text
    assert "240.0 s  6/8 models" in text and "200.0 s  Building wheel for llama-cpp-python" in text
    assert "phases" not in m.summary_text("macos", {"ttfs_seconds": 1.0})


def test_windows_setup_fetches_hymt_the_wizard_defers_1416():
    """#1416: since v0.4.6 the wizard runs `setup --default-listen --defer-mt` (Whisper only, #1359), so `tlwu` alone
    stopped with "Hy-MT2 1.8B is not installed". The setup step fetches Hy-MT2 like the app does (no --defer-mt), with
    the core tlwu.exe first and the venv tlwu-setup.exe as fallback."""
    m = _load()
    _install, setup, _tlwu = m.commands("windows", r"C:\t\tlwu", r"C:\dl\tinglongwu-setup.exe")
    fetch = setup.split(" & ", 1)[1]
    assert fetch == (r'if exist "C:\t\tlwu\tlwu.exe" ("C:\t\tlwu\tlwu.exe" setup --default-listen) else '
                     r'("C:\t\tlwu\.venv\Scripts\tlwu-setup.exe" --default-listen)')
    assert "--defer-mt" not in setup


def test_windows_summary_does_not_call_raw_source_time_deferred_1416():
    """#1416: on windows the raw first-source time includes the Hy-MT2 fetch in setup, so it is not labelled deferred."""
    m = _load()
    doc = {"first_source_subtitle_seconds": 90.0, "first_source_subtitle_baseline_seconds": 40.0}
    win, lin = m.summary_text("windows", doc), m.summary_text("linux", doc)
    assert "includes the Hy-MT2 fetch in setup" in win and "Hy-MT2 deferred" not in win
    assert "Hy-MT2 deferred" in lin
