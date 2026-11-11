"""#99: the installers (install.ps1, mac_install.sh), `tlwu-setup --selftest` and the exe's first-run window run
`tlwu --selftest` (#90) as their last step. A failed self-test only warns (nothing is deleted); a tlwu that has no
--selftest yet is reported as skipped. Every test runs the real code against a stub `tlwu` / `tlwu-setup`."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tinglongwu_setup import cli

ROOT = Path(__file__).resolve().parent.parent
COMMON_SH = ROOT / "scripts" / "tlwu_common.sh"
HELPERS = ROOT / "scripts" / "install_helpers.ps1"
PWSH = shutil.which("pwsh")
BASH = shutil.which("bash")

# Stub for `tlwu` / `tlwu-setup`: records its argv in $STUB_LOG, exits with $STUB_RC; rc 2 mimics an old tlwu whose
# argparse does not know --selftest.
STUB = """import json, os, sys
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
rc = int(os.environ.get("STUB_RC", "0"))
print("STUB-OUT rc=%d" % rc)
if rc == 2:
    sys.stderr.write("usage: tlwu [-h]\\ntlwu: error: unrecognized arguments: --selftest\\n")
sys.exit(rc)
"""


@pytest.fixture
def stub(tmp_path, monkeypatch):
    prog = tmp_path / "stub_tlwu.py"
    prog.write_text(STUB, encoding="utf-8")
    exe = tmp_path / "tlwu-setup"
    exe.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{prog}' \"$@\"\n")
    exe.chmod(0o755)
    log = tmp_path / "argv.log"
    monkeypatch.setenv("STUB_LOG", str(log))
    calls = lambda: [json.loads(x) for x in log.read_text("utf-8").splitlines()] if log.exists() else []  # noqa: E731
    return prog, exe, calls


# ---------------------------------------------------------------- tlwu-setup --selftest

def test_setup_selftest_forwards_to_tlwu_child_and_returns_its_code(stub, monkeypatch, capfd):
    prog, _, calls = stub
    monkeypatch.setattr(cli, "SELFTEST_CMD", [sys.executable, str(prog)])
    monkeypatch.setenv("STUB_RC", "0")
    assert cli.main(["--selftest", "--model", "small", "--engine", "opus"]) == 0
    monkeypatch.setenv("STUB_RC", "1")
    assert cli.main(["--selftest", "--model", "large-v3-turbo", "--device", "cpu", "--models-dir", "/m"]) == 1
    assert calls() == [["--selftest", "--model", "small", "--engine", "opus"],
                       ["--selftest", "--model", "large-v3-turbo", "--device", "cpu", "--models-dir", "/m"]]
    assert "STUB-OUT rc=1" in capfd.readouterr().out  # the child's output is not swallowed


def test_setup_selftest_on_a_tlwu_without_selftest_is_skipped_not_failed(stub, monkeypatch, capfd):
    prog, _, _ = stub
    monkeypatch.setattr(cli, "SELFTEST_CMD", [sys.executable, str(prog)])
    monkeypatch.setenv("STUB_RC", "2")
    assert cli.main(["--selftest"]) == cli.SELFTEST_UNAVAILABLE == 3
    assert "self-test skipped" in capfd.readouterr().out


def test_setup_selftest_child_never_loads_the_offline_runtime_in_setup(stub, tmp_path):
    prog, _, calls = stub
    code = ("import sys\nfrom tinglongwu_setup import cli\n"
            f"cli.SELFTEST_CMD = [sys.executable, {str(prog)!r}]\n"
            "rc = cli.main(['--selftest', '--model', 'small'])\n"
            "print('RC', rc, 'RUNTIME', 'tinglongwu' in sys.modules)\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT),
                       env={**os.environ, "STUB_RC": "0"}, timeout=120)
    assert "RC 0 RUNTIME False" in r.stdout, r.stdout + r.stderr
    assert calls() == [["--selftest", "--model", "small"]]


# ---------------------------------------------------------------- mac_install.sh step (tlwu_common.sh)

@pytest.mark.skipif(BASH is None, reason="bash not installed")
@pytest.mark.parametrize("rc,want_rc,want", [(0, 0, "self-test passed"), (3, 0, "no self-test yet"), (1, 1, None)])
def test_bash_install_selftest(stub, rc, want_rc, want):
    _, exe, calls = stub
    r = subprocess.run([BASH, "-c", f". '{COMMON_SH}'; tlwu_install_selftest '{exe}' --model small; echo RC=$?"],
                       capture_output=True, text=True, env={**os.environ, "STUB_RC": str(rc)}, timeout=60)
    out = r.stdout
    assert f"RC={want_rc}" in out and f"STUB-OUT rc={rc}" in out, out + r.stderr
    if want:
        assert want in out
    else:
        assert "passed" not in out and "skipped" not in out
    assert calls() == [["--selftest", "--model", "small"]]


# ---------------------------------------------------------------- install.ps1 step (install_helpers.ps1)

def _ps(script: str, timeout: int = 120) -> subprocess.CompletedProcess:
    env = {**os.environ, "POWERSHELL_TELEMETRY_OPTOUT": "1", "POWERSHELL_UPDATECHECK": "Off"}
    return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                          text=True, encoding="utf-8", timeout=timeout, env=env, cwd=str(ROOT))


@pytest.mark.skipif(PWSH is None, reason="pwsh not installed")
def test_powershell_install_selftest_verdicts(stub, monkeypatch):
    _, exe, calls = stub
    lines = []
    for rc in (0, 1, 3):
        monkeypatch.setenv("STUB_RC", str(rc))
        r = _ps(f". '{HELPERS}'\n$v = Invoke-TlwuSelftest '{exe}' @('--model', 'small', '--engine', 'opus')\n"
                "Write-Host ('VERDICT=' + $v)\n")
        assert r.returncode == 0, r.stderr
        lines.append([x for x in r.stdout.splitlines() if x.startswith("VERDICT=")])
        assert f"STUB-OUT rc={rc}" in r.stdout  # child output on the console, not in the return value
    assert lines == [["VERDICT=ok"], ["VERDICT=failed"], ["VERDICT=skipped"]]
    assert calls() == [["--selftest", "--model", "small", "--engine", "opus"]] * 3


@pytest.mark.skipif(PWSH is None, reason="pwsh not installed")
def test_install_dry_run_shows_the_selftest_as_last_step():
    r = _ps(f"& '{ROOT / 'install.ps1'}' -DryRun -Cpu -SkipShortcut; exit $LASTEXITCODE", timeout=300)
    lines = r.stdout.splitlines()
    if r.returncode != 0 and any("Python 3.10-3.12 not found" in x for x in lines):
        pytest.skip("no Python 3.10-3.12 on PATH for the installer's step 1")
    assert r.returncode == 0, r.stdout + r.stderr
    step8 = next(i for i, x in enumerate(lines) if x.startswith("[8/8] "))
    st = [x for x in lines[step8:] if "[dry-run]" in x and "--selftest" in x]
    assert len(st) == 1 and st[0].rstrip().endswith("--selftest --model small --engine hymt --device cpu")  # #301: Hy-MT2 on the CPU


# ---------------------------------------------------------------- exe: package data + first-run window

def test_exe_build_ships_tinglongwu_package_data():
    spec = importlib.util.spec_from_file_location("build_windows", ROOT / "packaging" / "build_windows.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "--include-package-data=tinglongwu" in mod.nuitka_cmd()
