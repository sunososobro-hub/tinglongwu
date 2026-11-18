"""#1469: ledger share/deficit math, mix parsing, zero-total case, and the CLI dry-run (stubbed gh)."""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ledger  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_share_and_deficit():
    rows = {r["type"]: r for r in ledger.build_rows({"bug": 25, "enhancement": 75}, ledger.DEFAULT_MIX)}
    assert rows["bug"]["share"] == 25.0 and rows["bug"]["deficit"] == 3.0
    assert rows["enhancement"]["share"] == 75.0 and rows["enhancement"]["deficit"] == -25.0
    assert rows["QA"]["deficit"] == 15.0


def test_zero_total_share_zero_deficit_is_target():
    for r in ledger.build_rows({}, ledger.DEFAULT_MIX):
        assert r["share"] == 0.0 and r["deficit"] == r["target"]
    assert ledger.infra_line(0, 0) == "infra: 0 / 0 = 0.0% (cap 10%)"


def test_infra_line():
    assert ledger.infra_line(10, 200) == "infra: 10 / 200 = 5.0% (cap 10%)"


def test_mix_parse_valid_and_invalid():
    body = "level: 2\nfocus: x\nmix: bug 30, enhancement 40, eval 10, documentation 10, maint 10"
    assert ledger.parse_mix(body) == ({"bug": 30, "enhancement": 40, "eval": 10, "documentation": 10, "maint": 10}, True)
    for bad in ["level: 2", "a\nb\nmix: bug 50, enhancement 60", "a\nb\nmix: bug 28, enhancement 50, eval 11, documentation 5, maint 5",
                "a\nb\nmix: bug x, enhancement 50, eval 11, documentation 5, maint 6"]:
        assert ledger.parse_mix(bad) == (ledger.DEFAULT_MIX, False)


def test_usage_minutes_last_line():
    assert ledger.usage_minutes("x\ntlwu-usage: model=sonnet tool_calls=3 minutes=12") == 12
    assert ledger.usage_minutes("no usage") is None


def test_cli_dry_run_with_stub_gh(tmp_path):
    stub = tmp_path / "gh"
    stub.write_text("#!/usr/bin/env python3\nimport sys, json\nu = sys.argv[2]\n"
                    "print(json.dumps({'body': 'level: 2\\nfocus: x\\nmix: bad'}) if u.endswith('/issues/574') else '[]')\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "ledger.py"), "--dry-run"],
                         capture_output=True, text=True, env=env, check=True).stdout
    assert "| bug | 0 | 0.0% | 28% | +28.0 |" in out
    assert "mix invalid, default used" in out and "infra: 0 / 0 = 0.0% (cap 10%)" in out
