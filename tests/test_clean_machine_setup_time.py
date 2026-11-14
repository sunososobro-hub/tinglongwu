"""#769: clean_machine_check.py setup-time - pure judge / log parsing / sandbox script generation."""
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
import clean_machine_check as cmc  # noqa: E402

READY = "READY default-listen seconds=95.0 download_mb=1100"


def test_constants():
    assert cmc.SETUP_READY_BUDGET_S == 60 and cmc.BASELINE_MBPS == 100


def test_parse_log_ready_and_pip():
    r = cmc.parse_install_log(f"[1/7] x\n{READY}\n")
    assert r == {"ready": True, "download_seconds": 95.0, "download_mb": 1100, "pip_lines": 0}
    assert cmc.parse_install_log("python -m pip install foo\nPIP install x\n")["pip_lines"] == 2
    assert cmc.parse_install_log("")["ready"] is False


def test_judge_ok_excludes_download():
    res = cmc.build_result(150.0, f"{READY}\n")  # 150 - 95 = 55 s <= 60
    lines = cmc.judge(res)
    assert not any(ln.startswith("FAIL") for ln in lines)


def test_judge_fails():
    slow = cmc.judge(cmc.build_result(170.0, f"{READY}\n"))  # 75 s
    assert any(ln.startswith("FAIL") and "budget" in ln for ln in slow)
    pip = cmc.judge(cmc.build_result(100.0, f"pip install x\n{READY}\n"))
    assert any(ln.startswith("FAIL") and "pip" in ln for ln in pip)
    notready = cmc.judge(cmc.build_result(10.0, ""))
    assert any(ln.startswith("FAIL") and "ready" in ln for ln in notready)


def test_sandbox_files_and_collect(tmp_path):
    setup = tmp_path / "dl" / "tlwu-setup.exe"
    setup.parent.mkdir()
    setup.write_bytes(b"x")
    out = tmp_path / "out"
    wsb = cmc.write_setup_sandbox(setup, out)
    root = ET.parse(wsb).getroot()
    assert root.find("Networking").text == "Enable"
    assert "tlwu-setup.exe" in (out / "run_setup.ps1").read_text("utf-8")
    assert cmc.collect_setup_result(out) is None
    (out / "setup_seconds.txt").write_text("150", "utf-8")
    (out / "install.log").write_text(READY, "utf-8")
    res = cmc.collect_setup_result(out)
    assert json.loads((out / "setup_time.json").read_text("utf-8")) == res
    assert set(res) == {"setup_seconds", "download_seconds", "download_mb", "ready", "pip_lines"}


def test_cli_without_run(tmp_path, capsys):
    s = tmp_path / "s.exe"
    s.write_bytes(b"x")
    assert cmc.main(["setup-time", str(s), "--out", str(tmp_path / "o")]) == 0
    assert (tmp_path / "o" / "setup-time.wsb").exists()
