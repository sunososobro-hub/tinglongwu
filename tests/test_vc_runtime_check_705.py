"""#705: the clean-Windows CI judge (scripts/vc_runtime_check.py) fails the old v0.3.2 behaviour and passes the fix."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("vc_runtime_check", ROOT / "scripts" / "vc_runtime_check.py")
vc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vc)

MISSING = "msvcp140.dll,vcruntime140_1.dll,vcomp140.dll"

# What v0.3.2 did on a PC without the runtime (log from the issue): self-check fails, carries on to the models, exit 0.
OLD_LOG = """[4/8] GPU libraries and Hy-MT2 translation engine
  !!  llama-cpp-python failed the self-check: llama-cpp-python cannot be loaded (Failed to load shared library '...\\llama.dll': Could not find module ...)
  !!  No usable llama-cpp-python CPU build: tlwu cannot translate until it is fixed (run '.venv\\Scripts\\tlwu-setup.exe --check-llama', or re-run install.bat).
[5/8] Download models (the slow part; shows percent done)
      -SkipModels: skipped
"""
OLD_RESULT = {"vc_missing_start": MISSING, "install_rc": "0", "check_llama_rc": "1"}

FIXED_LOG = """[4/8] GPU libraries and Hy-MT2 translation engine
      Microsoft Visual C++ runtime is missing (msvcp140.dll); the translation engine needs it.
  OK  Microsoft Visual C++ runtime installed / 已安裝
  OK  llama-cpp-python already works
[5/8] Download models (the slow part; shows percent done)
"""
BLOCKED_LOG = """[4/8] GPU libraries and Hy-MT2 translation engine
  !!  VC++ runtime install failed: The remote name could not be resolved: 'aka.ms'
  XX  缺少 Microsoft Visual C++ 執行階段
      The Microsoft Visual C++ 2015-2022 runtime is missing and could not be installed, so the translation engine cannot load. Stopped before the model download. Install it from https://aka.ms/vs/17/release/vc_redist.x64.exe, then run the repair install.
"""


@pytest.mark.parametrize("mode", vc.MODES)
def test_old_behaviour_fails_both_modes(mode):
    problems = vc.evaluate(mode, OLD_RESULT, OLD_LOG)
    assert problems, "the v0.3.2 silent carry-on must be red"
    if mode == "blocked":
        assert any("step 5" in p for p in problems) and any("exit code 0" in p for p in problems)
    else:
        assert any("installed" in p for p in problems) and any("check-llama" in p for p in problems)


def test_fixed_install_passes():
    assert vc.evaluate("installs", {"vc_missing_start": MISSING, "install_rc": "0", "check_llama_rc": "0"}, FIXED_LOG) == []


def test_fixed_forced_failure_passes():
    assert vc.evaluate("blocked", {"vc_missing_start": MISSING, "install_rc": "1", "check_llama_rc": ""}, BLOCKED_LOG) == []


def test_machine_with_runtime_proves_nothing():
    problems = vc.evaluate("installs", {"vc_missing_start": "", "install_rc": "0", "check_llama_rc": "0"}, FIXED_LOG)
    assert problems == ["no VC++ runtime DLL was missing before the install: this machine does not reproduce #705"]


def test_installed_but_llama_still_broken_fails():
    problems = vc.evaluate("installs", {"vc_missing_start": MISSING, "install_rc": "0", "check_llama_rc": "1"}, FIXED_LOG)
    assert len(problems) == 1 and "check-llama" in problems[0]


def test_cli_reads_files_and_utf16_log(tmp_path, capsys):
    (tmp_path / "result.txt").write_text("﻿vc_missing_start=msvcp140.dll\ninstall_rc=1\ncheck_llama_rc=\n", encoding="utf-8")
    (tmp_path / "install.log").write_bytes(BLOCKED_LOG.encode("utf-16"))
    rc = vc.main(["--mode", "blocked", "--result", str(tmp_path / "result.txt"), "--log", str(tmp_path / "install.log")])
    assert rc == 0 and "PASS #705 (blocked)" in capsys.readouterr().out
    rc = vc.main(["--mode", "installs", "--result", str(tmp_path / "missing.txt"), "--log", str(tmp_path / "install.log")])
    assert rc == 1
