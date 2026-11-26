"""#1728: the emulator microphone step of mobile-emulator.yml and mobile/tool/host_mic.sh `serve`.

Runs the real step script under the shell GitHub uses (`bash -e -o pipefail`), with stub `flutter` / `adb` /
`paplay` on PATH: the stub test writes mic_ready / mic_clip_done into the fake device dir and waits for the files
the host writes through `adb shell touch`, so the handshake order (grant -> clip -> mic_go -> clip_done -> stop -> mic_silent) is
exercised end to end without an emulator."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github/workflows/mobile-emulator.yml"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None or os.name == "nt", reason="needs a POSIX bash")


def _wf() -> dict:
    return yaml.safe_load(WF.read_text(encoding="utf-8"))


def _step(name_prefix: str) -> dict:
    return next(s for s in _wf()["jobs"]["two-way"]["steps"] if s.get("name", "").startswith(name_prefix))


def _stub(bin_dir: Path, name: str, body: str) -> None:
    p = bin_dir / name
    p.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8")
    p.chmod(0o755)


def _env(tmp: Path, bin_dir: Path) -> dict:
    env = {k: str(v) for k, v in _wf()["env"].items()}
    env.update({"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp), "RUNNER_TEMP": str(tmp / "rt"),
                "ANDROID_HOME": str(tmp / "sdk"), "TLWU_PULSE_DIR": str(tmp / "pulse"),
                "GITHUB_STEP_SUMMARY": str(tmp / "summary.md")})
    return env


def _mic_step(tmp: Path, flutter_body: str) -> tuple[subprocess.CompletedProcess, str]:
    bin_dir = tmp / "bin"; bin_dir.mkdir()
    dev = tmp / "dev"  # stands in for the emulator's file system
    calls = tmp / "adb.calls"
    adb_dir = tmp / "sdk" / "platform-tools"; adb_dir.mkdir(parents=True)
    _stub(adb_dir, "adb", f'echo "$*" >> "{calls}"\n'
                          f'if [ "$1 $2" = "shell touch" ]; then mkdir -p "{dev}$(dirname "$3")"; touch "{dev}$3"; fi\n'
                          f'if [ "$1 $2" = "shell ls" ]; then ls "{dev}$3"; fi')
    _stub(bin_dir, "paplay", "sleep 0.2")
    _stub(bin_dir, "flutter", flutter_body.replace("@DEV@", str(dev)))
    (tmp / "rt" / "e2e").mkdir(parents=True)
    (tmp / "rt" / "e2e" / "zh.wav").write_bytes(b"RIFF")
    step = _step("Integration test (host clip")
    env = {**_env(tmp, bin_dir), **{k: str(v) for k, v in step.get("env", {}).items()}}
    r = subprocess.run([BASH, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
                       cwd=ROOT / "mobile", env=env, capture_output=True, text=True, timeout=60)
    return r, calls.read_text(encoding="utf-8") if calls.exists() else ""


# The stub `flutter test`: the same files as mic_test.dart, and only one print, at the end (as flutter test does).
FAKE_TEST = """D="@DEV@/sdcard/Android/data/$PKG/files/e2e"; mkdir -p "$D"
w() { for _ in $(seq 1 100); do [ -f "$D/$1" ] && return 0; sleep 0.1; done; echo "no $1"; exit 7; }
touch "$D/mic_ready"; w mic_go
touch "$D/mic_clip_done"; w mic_silent
echo "TLWU_MIC_RMS clip=-21.3 silent=-120.0 threshold=-60.0"
"""


def test_mic_step_handshake_order(tmp_path):
    r, calls = _mic_step(tmp_path, FAKE_TEST)
    assert r.returncode == 0, r.stdout + r.stderr
    pkg = _wf()["env"]["PKG"]
    order = [line for line in calls.splitlines() if line.startswith("shell ") and not line.startswith("shell ls")]
    assert order == [f"shell pm grant {pkg} android.permission.RECORD_AUDIO",
                     f"shell touch /sdcard/Android/data/{pkg}/files/e2e/mic_go",
                     f"shell touch /sdcard/Android/data/{pkg}/files/e2e/mic_silent"], calls
    assert "TLWU_MIC_RMS clip=-21.3" in (tmp_path / "rt" / "mic.log").read_text(encoding="utf-8")
    assert not (tmp_path / "pulse" / "play.pid").exists()  # the clip loop was stopped


def test_mic_step_fails_when_the_test_fails(tmp_path):
    r, _ = _mic_step(tmp_path, 'exit 3')
    assert r.returncode == 3, r.stdout + r.stderr


def test_summary_reports_mic_rms(tmp_path):
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    (tmp_path / "rt").mkdir()
    (tmp_path / "rt" / "mic.log").write_text("x\nTLWU_MIC_RMS clip=-21.3 silent=-120.0 threshold=-60.0\n",
                                             encoding="utf-8")
    r = subprocess.run([BASH, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", _step("Job summary")["run"]],
                       cwd=tmp_path, env=_env(tmp_path, bin_dir), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    s = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "**TLWU_MIC_RMS clip=-21.3 silent=-120.0 threshold=-60.0**" in s


def test_summary_mic_rms_na_without_log(tmp_path):
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    (tmp_path / "rt").mkdir()
    r = subprocess.run([BASH, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", _step("Job summary")["run"]],
                       cwd=tmp_path, env=_env(tmp_path, bin_dir), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "emulator mic RMS dBFS" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_emulator_starts_windowed_on_xvfb(tmp_path):
    """Round 2: the headless qemu (-no-window) never opened PulseAudio, so the step runs the windowed build under
    xvfb-run and records which qemu binaries link libpulse."""
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    (tmp_path / "rt").mkdir()
    sdk = tmp_path / "sdk"
    xcalls = tmp_path / "xvfb.calls"
    _stub(bin_dir, "xvfb-run", f'echo "$*" >> "{xcalls}"')
    _stub(bin_dir, "ldd", 'case "$1" in *headless) echo "libc.so.6 => /lib/libc.so.6";; '
                          '*) echo "libpulse.so.0 => /usr/lib/libpulse.so.0 (0x1)";; esac')
    tools = sdk / "cmdline-tools" / "latest" / "bin"; tools.mkdir(parents=True)
    _stub(tools, "avdmanager", 'mkdir -p "$ANDROID_AVD_HOME/e2e.avd"; touch "$ANDROID_AVD_HOME/e2e.avd/config.ini"')
    (sdk / "platform-tools").mkdir(parents=True)
    _stub(sdk / "platform-tools", "adb", 'if [ "$2" = getprop ]; then echo 1; fi')
    qd = sdk / "emulator" / "qemu" / "linux-x86_64"; qd.mkdir(parents=True)
    _stub(qd, "qemu-system-x86_64", "true")
    _stub(qd, "qemu-system-x86_64-headless", "true")
    r = subprocess.run([BASH, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c",
                        _step("Start the emulator")["run"]],
                       cwd=tmp_path, env=_env(tmp_path, bin_dir), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    for _ in range(50):  # the emulator is started with nohup ... &
        if xcalls.exists():
            break
        subprocess.run(["sleep", "0.1"])
    args = xcalls.read_text(encoding="utf-8").split()
    assert args[0] == "-a" and f"{sdk}/emulator/emulator" in args
    emu_args = args[args.index(f"{sdk}/emulator/emulator") + 1:]
    assert "-no-window" not in emu_args and "-allow-host-audio" in emu_args
    cfg = (tmp_path / ".android" / "avd" / "e2e.avd" / "config.ini").read_text(encoding="utf-8")
    assert "hw.audioInput=yes" in cfg
    qa = (tmp_path / "rt" / "qemu-audio.txt").read_text(encoding="utf-8")
    assert "qemu-system-x86_64: libpulse.so.0 => /usr/lib/libpulse.so.0" in qa
    assert "qemu-system-x86_64-headless: \n" in qa


def test_summary_lists_qemu_audio(tmp_path):
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    (tmp_path / "rt").mkdir()
    (tmp_path / "rt" / "qemu-audio.txt").write_text("qemu-system-x86_64: libpulse.so.0 => /x\n", encoding="utf-8")
    r = subprocess.run([BASH, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", _step("Job summary")["run"]],
                       cwd=tmp_path, env=_env(tmp_path, bin_dir), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "- qemu audio: qemu-system-x86_64: libpulse.so.0 => /x" in (tmp_path / "summary.md").read_text("utf-8")
