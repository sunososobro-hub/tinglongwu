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
