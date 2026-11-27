"""Shared runner for tests that start pwsh (#2035).

pwsh sometimes crashes on the CI runner before running the script: exit 0 and the banner
"An error has occurred that was not properly handled" on stdout. `run_pwsh` retries exactly once
on that banner or on exit 0 with empty stdout and stderr (#2045); a second crash fails with the banner in the message, any real script error is
returned untouched for the test to assert on.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

CRASH_BANNER = "An error has occurred that was not properly handled"


class PwshCrash(AssertionError):
    """pwsh crashed (banner printed, or exit 0 with empty output) on both attempts."""


def _text(stream) -> str:
    if isinstance(stream, bytes):
        stream = stream.decode("utf-8", "replace")
    return stream or ""


def _silent_crash(r: subprocess.CompletedProcess) -> bool:
    """pwsh died without the banner: exit 0 and nothing but whitespace on stdout and stderr (#2045)."""
    return r.returncode == 0 and not _text(r.stdout).strip() and not _text(r.stderr).strip()


def _has_banner(r: subprocess.CompletedProcess) -> bool:
    for stream in (r.stdout, r.stderr):
        if isinstance(stream, bytes):
            stream = stream.decode("utf-8", "replace")
        if stream and CRASH_BANNER in stream:
            return True
    return False


def run_pwsh(args, *, env=None, pwsh=None, timeout=120, **kwargs) -> subprocess.CompletedProcess:
    """Run pwsh with `-NoProfile -NonInteractive` plus `args` (list); capture_output is always on.

    `pwsh` is the executable (str) or a command prefix (list, used by the unit test's fake).
    `env` (default os.environ) gets telemetry / update-check off and a fresh PSModuleAnalysisCachePath
    per call. Other kwargs (text, encoding, cwd) go to subprocess.run.
    """
    exe = pwsh or shutil.which("pwsh")
    assert exe, "pwsh is not installed"
    prefix = [exe] if isinstance(exe, str) else list(exe)
    r = None
    for _attempt in range(2):
        with tempfile.TemporaryDirectory(prefix="tlwu-pwsh-") as cache:
            e = {**(os.environ if env is None else env), "POWERSHELL_TELEMETRY_OPTOUT": "1",
                 "POWERSHELL_UPDATECHECK": "Off", "PSModuleAnalysisCachePath": os.path.join(cache, "analysis.cache")}
            r = subprocess.run(prefix + ["-NoProfile", "-NonInteractive", *args], capture_output=True,
                               timeout=timeout, env=e, **kwargs)
        banner = _has_banner(r)
        if not banner and not _silent_crash(r):
            return r
        reason = f"'{CRASH_BANNER}'" if banner else "empty output"
    raise PwshCrash(f"pwsh crashed twice with {reason}; stdout={r.stdout!r} stderr={r.stderr!r}")
