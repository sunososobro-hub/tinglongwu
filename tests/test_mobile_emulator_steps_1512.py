"""#1512: mobile-emulator.yml step scripts, run under the same shell GitHub uses (`bash -e -o pipefail`).

Runs 45765117770 / 45778290378 failed on runner facts, not on the app: Maven Central 429 during the APK build,
`ffmpeg: command not found` on ubuntu-24.04, and the always() job summary then failed too because test.log
and the app log did not exist. Each test runs the real step script with stub tools on PATH."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
WF = Path(__file__).resolve().parent.parent / ".github/workflows/mobile-emulator.yml"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None or os.name == "nt", reason="needs a POSIX bash")


def _step(name_prefix: str) -> str:
    steps = yaml.safe_load(WF.read_text(encoding="utf-8"))["jobs"]["two-way"]["steps"]
    return next(s["run"] for s in steps if s.get("name", "").startswith(name_prefix))


def _stub(bin_dir: Path, name: str, body: str) -> None:
    p = bin_dir / name
    p.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8")
    p.chmod(0o755)


def _run(script: str, tmp: Path, bin_dir: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp), "RUNNER_TEMP": str(tmp / "rt"),
           "ANDROID_HOME": str(tmp / "sdk"), "PKG": "com.tinglongwu.mobile",
           "GITHUB_STEP_SUMMARY": str(tmp / "summary.md"), **(extra_env or {})}
    (tmp / "rt").mkdir(exist_ok=True)
    return subprocess.run([BASH, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
                          cwd=tmp, env=env, capture_output=True, text=True, timeout=60)


def test_summary_survives_missing_logs(tmp_path):
    """No test.log and no adb (an earlier step failed): the summary step still exits 0 and says n/a."""
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    r = _run(_step("Job summary"), tmp_path, bin_dir)
    assert r.returncode == 0, r.stderr
    assert "**n/a ms**" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_summary_reports_speaklat(tmp_path):
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    (tmp_path / "rt").mkdir()
    (tmp_path / "rt" / "test.log").write_text("x\nTLWU_SPEAKLAT=812 samples=4410\n", encoding="utf-8")
    r = _run(_step("Job summary"), tmp_path, bin_dir)
    assert r.returncode == 0, r.stderr
    assert "**812 ms**" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def _build(tmp_path: Path, fails: int) -> tuple[subprocess.CompletedProcess, int]:
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    n = tmp_path / "n"
    _stub(bin_dir, "flutter", f'c=$(cat "{n}" 2>/dev/null || echo 0); c=$((c+1)); echo $c > "{n}"; '
                              f'[ "$TLWU_X86_64" = 1 ] || exit 9; [ $c -gt {fails} ]')
    _stub(bin_dir, "sleep", "exit 0")
    r = _run(_step("Debug APK"), tmp_path, bin_dir)
    return r, int(n.read_text())


def test_build_retries_once_after_a_network_failure(tmp_path):
    r, calls = _build(tmp_path, fails=1)
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls == 2


def test_build_fails_after_two_failures(tmp_path):
    r, calls = _build(tmp_path, fails=5)
    assert r.returncode != 0
    assert calls == 2


def test_inputs_step_installs_ffmpeg_when_missing(tmp_path):
    """The inputs step installs ffmpeg via apt before converting the clip when the runner lacks it."""
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    log = tmp_path / "calls.log"
    # `sudo apt-get ... install ... ffmpeg` drops a working ffmpeg stub on PATH, like the real package would.
    _stub(bin_dir, "sudo", f'echo "sudo $*" >> "{log}"; '
                           f'if [[ "$*" == *install*ffmpeg* ]]; then printf \'#!/bin/bash\\necho "ffmpeg $*" >> "{log}"\\n\' '
                           f'> "{bin_dir}/ffmpeg"; chmod +x "{bin_dir}/ffmpeg"; fi')
    for tool in ("gh", "curl", "sha256sum", "tar"):
        _stub(bin_dir, tool, f'echo "{tool} $*" >> "{log}"')
    core = tmp_path / "core"; core.mkdir()  # PATH without the host's own ffmpeg
    for tool in ("mkdir", "ls", "chmod", "cat"):
        (core / tool).symlink_to(shutil.which(tool))
    r = _run(_step("Test inputs"), tmp_path, bin_dir,
             {"SV_URL": "https://example.invalid/sv.tar.bz2", "SV_SHA": "0" * 64, "PATH": f"{bin_dir}:{core}"})
    assert r.returncode == 0, r.stdout + r.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    i_apt = next(i for i, c in enumerate(calls) if c.startswith("sudo") and "install" in c and "ffmpeg" in c)
    i_ff = next(i for i, c in enumerate(calls) if c.startswith("ffmpeg "))
    assert i_apt < i_ff
