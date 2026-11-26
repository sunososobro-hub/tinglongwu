"""#1512: mobile-emulator.yml step scripts, run under the same shell GitHub uses (`bash -e -o pipefail`).

Runs 45765117770 / 45778290378 failed on runner facts, not on the app: Maven Central 429 during the APK build,
`ffmpeg: command not found` on ubuntu-24.04, and the always() job summary then failed too because test.log
and the app log did not exist. Each test runs the real step script with stub tools on PATH."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
WF = Path(__file__).resolve().parent.parent / ".github/workflows/mobile-emulator.yml"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None or os.name == "nt", reason="needs a POSIX bash")


def _step(name_prefix: str) -> str:
    steps = yaml.safe_load(WF.read_text(encoding="utf-8"))["jobs"]["two-way"]["steps"]
    return next(s["run"] for s in steps if s.get("name", "").startswith(name_prefix))


def _wf_env() -> dict:
    """The workflow-level env (PKG, ACTIVITY, ...) exactly as Actions passes it to every step."""
    return {k: str(v) for k, v in yaml.safe_load(WF.read_text(encoding="utf-8"))["env"].items()}


def _stub(bin_dir: Path, name: str, body: str) -> None:
    p = bin_dir / name
    p.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8")
    p.chmod(0o755)


def _run(script: str, tmp: Path, bin_dir: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp), "RUNNER_TEMP": str(tmp / "rt"),
           "ANDROID_HOME": str(tmp / "sdk"), **_wf_env(),
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


def test_emulator_step_pins_one_avd_home(tmp_path):
    """Run 45787449295: avdmanager wrote the AVD under another home than ~/.android/avd and the config.ini
    append failed. The step must pin ANDROID_AVD_HOME so avdmanager, the config edit and the emulator agree."""
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    sdk = tmp_path / "sdk"
    log = tmp_path / "calls.log"
    # Like the runner image: without ANDROID_AVD_HOME the AVD lands under ~/.config/.android/avd.
    (sdk / "cmdline-tools/latest/bin").mkdir(parents=True)
    _stub(sdk / "cmdline-tools/latest/bin", "avdmanager",
          'h="${ANDROID_AVD_HOME:-$HOME/.config/.android/avd}"; n=""; while [ $# -gt 0 ]; do '
          '[ "$1" = -n ] && n=$2; shift; done; mkdir -p "$h/$n.avd"; echo "hw.lcd.density=420" > "$h/$n.avd/config.ini"')
    (sdk / "emulator").mkdir()
    _stub(sdk / "emulator", "emulator", f'echo "emulator AVD_HOME=$ANDROID_AVD_HOME $*" >> "{log}"')
    (sdk / "platform-tools").mkdir()
    _stub(sdk / "platform-tools", "adb", '[ "$1 $2" = "shell getprop" ] && echo 1; exit 0')
    _stub(bin_dir, "timeout", 'shift; exec "$@"')
    _stub(bin_dir, "sleep", "exit 0")
    r = _run(_step("Start the emulator"), tmp_path, bin_dir, {"SYS_IMAGE": "system-images;android-34;google_apis;x86_64"})
    assert r.returncode == 0, r.stdout + r.stderr
    cfg = tmp_path / ".android/avd/e2e.avd/config.ini"
    text = cfg.read_text(encoding="utf-8")
    assert "hw.lcd.density=420" in text and "hw.ramSize=6144" in text
    for _ in range(50):  # the emulator is started in the background (nohup ... &)
        if log.exists() and log.read_text(encoding="utf-8"):
            break
        time.sleep(0.1)
    assert f"AVD_HOME={tmp_path}/.android/avd -avd e2e" in log.read_text(encoding="utf-8")


# What the debug APK installs as (mobile/android/app/build.gradle.kts: applicationIdSuffix ".dev", #1600;
# the Kotlin namespace, hence the activity class, keeps com.tinglongwu.mobile).
_DEBUG_ID = "com.tinglongwu.mobile.dev"
_ACTIVITY = "com.tinglongwu.mobile.MainActivity"


def _install_step(tmp_path: Path) -> tuple[subprocess.CompletedProcess, str]:
    """Run the install step against a stub adb that behaves like a device with the debug APK installed:
    `pm path` knows only the .dev id, `am start` prints "Error type 3" (and exits 0) for any other component."""
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    sdk = tmp_path / "sdk"; (sdk / "platform-tools").mkdir(parents=True)
    log = tmp_path / "adb.log"
    _stub(sdk / "platform-tools", "adb", f'''echo "$*" >> "{log}"
case "$1 $2 $3" in
  "shell pm path") [ "$4" = "{_DEBUG_ID}" ] && echo "package:/data/app/x/base.apk"; exit 0;;
  "shell pm list") echo "package:{_DEBUG_ID}"; exit 0;;
  "shell am start") [ "$6" = "{_DEBUG_ID}/{_ACTIVITY}" ] && echo "Status: ok" || echo "Error type 3"; exit 0;;
esac
exit 0''')
    _stub(bin_dir, "sleep", "exit 0")
    (tmp_path / "rt/e2e").mkdir(parents=True)
    (tmp_path / "rt/e2e/zh.wav").write_bytes(b"x")
    r = _run(_step("Install the app"), tmp_path, bin_dir)
    return r, log.read_text(encoding="utf-8") if log.exists() else ""


def test_install_step_pushes_into_the_debug_apks_dir(tmp_path):
    """Run 45795806871: inputs were pushed under com.tinglongwu.mobile while the debug APK is ...mobile.dev,
    so the test saw no zip. The step must launch the debug app and push into its files dir."""
    r, calls = _install_step(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"am start -W -n {_DEBUG_ID}/{_ACTIVITY}" in calls
    assert f"push {tmp_path}/rt/e2e/zh.wav /sdcard/Android/data/{_DEBUG_ID}/files/e2e/" in calls


def test_install_step_fails_when_pkg_is_not_installed(tmp_path, monkeypatch):
    """A wrong PKG must stop the step with an error, not push into a dir the app never reads."""
    real = _wf_env

    def wrong_pkg():
        return {**real(), "PKG": "com.tinglongwu.mobile"}
    monkeypatch.setitem(globals(), "_wf_env", wrong_pkg)
    r, calls = _install_step(tmp_path)
    assert r.returncode != 0
    assert "is not installed" in r.stdout + r.stderr
    assert " push " not in f" {calls}"
