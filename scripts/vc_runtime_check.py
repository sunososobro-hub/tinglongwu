"""#705: judge one clean-Windows install run (no Microsoft VC++ runtime) from its install log and result file.

Used by .github/workflows/vc-runtime-clean.yml. The container writes ``result.txt`` (``key=value`` lines):
  vc_missing_start  runtime DLLs missing from System32 before install.bat ran (comma list; must not be empty)
  install_rc        install.bat exit code
  check_llama_rc    exit code of ``.venv\\Scripts\\tlwu-setup.exe --check-llama`` ('' when not run)

Modes:
  installs  aka.ms reachable: step 4 must install the runtime, install.bat exits 0, --check-llama passes.
  blocked   aka.ms unreachable (forced failure): install.bat must stop at step 4 with a non-zero exit, name the
            missing runtime, and never reach step 5 (the 2 GB model download).

    python scripts/vc_runtime_check.py --mode installs --result out/result.txt --log out/install.log
Exit 0 and ``PASS`` when the run behaves as above, else exit 1 with one ``FAIL:`` line per problem.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

INSTALLED = "Microsoft Visual C++ runtime installed"
STOPPED = "Stopped before the model download"
STEP5 = "[5/8]"
MODES = ("installs", "blocked")


def parse_result(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, val = line.strip().lstrip("﻿").partition("=")
        if sep:
            out[key.strip()] = val.strip()
    return out


def _rc(result: dict[str, str], key: str) -> int | None:
    try:
        return int(result.get(key, ""))
    except ValueError:
        return None


def evaluate(mode: str, result: dict[str, str], log: str) -> list[str]:
    """Problems with this run (empty list = PASS)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    problems: list[str] = []
    if not result.get("vc_missing_start", "").strip(", "):
        problems.append("no VC++ runtime DLL was missing before the install: this machine does not reproduce #705")
    rc = _rc(result, "install_rc")
    if mode == "installs":
        if INSTALLED not in log:
            problems.append(f"step 4 did not report '{INSTALLED}'")
        if rc != 0:
            problems.append(f"install.bat exit code {rc}, expected 0")
        if _rc(result, "check_llama_rc") != 0:
            problems.append(f"tlwu-setup --check-llama exit code {result.get('check_llama_rc', '')!r}, expected 0")
    else:
        if rc in (None, 0):
            problems.append(f"install.bat exit code {rc}, expected non-zero (install must stop)")
        if STOPPED not in log or "Visual C++" not in log:
            problems.append("no on-screen message naming the missing Visual C++ runtime and the stop before the models")
        if STEP5 in log:
            problems.append("install carried on to step 5 (model download) without a working translator")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=MODES, required=True)
    ap.add_argument("--result", type=Path, required=True)
    ap.add_argument("--log", type=Path, required=True)
    a = ap.parse_args(argv)
    result = parse_result(a.result.read_text(encoding="utf-8", errors="replace")) if a.result.exists() else {}
    raw = a.log.read_bytes() if a.log.exists() else b""
    log = raw.decode("utf-16", errors="replace") if b"\x00" in raw[:200] else raw.decode("utf-8", errors="replace")
    problems = evaluate(a.mode, result, log)
    for p in problems:
        print(f"FAIL: {p}")
    if not problems:
        print(f"PASS #705 ({a.mode}): {result}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
