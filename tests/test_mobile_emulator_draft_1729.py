"""#1729: the grey-draft step of mobile-emulator.yml and its job summary lines, run under `bash -e -o pipefail`
with stub tools (as tests/test_mobile_emulator_steps_1512.py)."""
from __future__ import annotations

from test_mobile_emulator_steps_1512 import _run, _step, _stub, pytestmark  # noqa: F401

LOG = (
    "TLWU_DRAFT_ROUND draft=off firstDraft=- firstFinal=4100 lat=3200\n"
    "TLWU_DRAFT_ROUND draft=on firstDraft=1700 firstFinal=4300 lat=3400\n"
    "TLWU_DRAFT_LAT on=3400 off=3200 margin=1500 within=yes on_runs=3400 off_runs=3200 warmup_lat=9000\n"
)


def test_step_runs_draft_test_and_keeps_its_exit_code(tmp_path):
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    _stub(bin_dir, "flutter", 'echo "args=$*"; echo TLWU_DRAFT_LAT on=1 off=2; exit 3')
    _stub(bin_dir, "sleep", "exit 0")
    adb_dir = tmp_path / "sdk" / "platform-tools"; adb_dir.mkdir(parents=True)
    calls = tmp_path / "adb_calls"
    _stub(adb_dir, "adb", f'echo "$*" >> "{calls}"')
    (tmp_path / "rt" / "e2e").mkdir(parents=True)
    (tmp_path / "rt" / "e2e" / "zh.wav").write_bytes(b"x")
    r = _run(_step("Integration test (grey source draft"), tmp_path, bin_dir)
    assert r.returncode == 3
    adb = calls.read_text(encoding="utf-8").splitlines()
    # Run 45906708811: the inputs are pushed again (after a launch) before this test runs.
    assert adb[0].startswith("install -r -t ")
    assert any(a.startswith("shell am start") for a in adb)
    assert adb[-1].startswith("push ") and adb[-1].endswith("/files/e2e/")
    log = (tmp_path / "rt" / "draft.log").read_text(encoding="utf-8")
    assert "args=test integration_test/draft_test.dart -d emulator-5554" in log
    assert "TLWU_DRAFT_LAT on=1 off=2" in log


def test_summary_reports_draft_medians_and_rounds(tmp_path):
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    (tmp_path / "rt").mkdir()
    (tmp_path / "rt" / "draft.log").write_text("noise\n" + LOG, encoding="utf-8")
    r = _run(_step("Job summary"), tmp_path, bin_dir)
    assert r.returncode == 0, r.stderr
    s = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "**on=3400 off=3200 margin=1500 within=yes" in s
    assert "- draft round: draft=on firstDraft=1700 firstFinal=4300 lat=3400" in s


def test_summary_without_draft_log_says_na(tmp_path):
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    r = _run(_step("Job summary"), tmp_path, bin_dir)
    assert r.returncode == 0, r.stderr
    s = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "off (ms, report only): **n/a**" in s
    assert "draft round:" not in s
