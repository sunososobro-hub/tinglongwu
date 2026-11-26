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


def _route(tmp: Path, short_outputs: str) -> tuple[subprocess.CompletedProcess, str, str]:
    bin_dir = tmp / "bin"; bin_dir.mkdir()
    pcalls, acalls = tmp / "pactl.calls", tmp / "adb.calls"
    _stub(bin_dir, "pactl", f'echo "$*" >> "{pcalls}"\n'
                            f'case "$*" in\n'
                            f'  "list short source-outputs") printf "{short_outputs}";;\n'
                            f'  "list source-outputs") printf "Source Output #7\\n\\tSource: 3\\n\\tCorked: no\\n'
                            f'\\tMute: no\\n\\t\\tapplication.process.binary = \\"qemu-system-x86_64\\"\\n";;\n'
                            f'  "list short sources") printf "3\\ttlwu_clip.monitor\\tmodule-null-sink.c\\ts16le\\tIDLE\\n";;\n'
                            f'esac')
    _stub(bin_dir, "adb", f'echo "$*" >> "{acalls}"; echo OK')
    env = {**_env(tmp, bin_dir), "ADB": str(bin_dir / "adb")}
    r = subprocess.run([BASH, str(ROOT / "mobile/tool/host_mic.sh"), "route"], env=env, capture_output=True,
                       text=True, timeout=30)
    read = lambda p: p.read_text(encoding="utf-8") if p.exists() else ""
    return r, read(pcalls), read(acalls)


def test_route_turns_host_mic_on_and_moves_qemu_stream_to_the_clip(tmp_path):
    """Round 3: run 45881839662 had qemu's PulseAudio record stream open but the guest heard -76.5 dBFS with and
    without the clip, so `route` turns the emulator's host mic on and puts every record stream on the clip's
    monitor, unmuted at 100 %, and logs what PulseAudio reports for the job summary."""
    r, pcalls, acalls = _route(tmp_path, "7\\t3\\t12\\tprotocol-native.c\\ts16le 2ch 44100Hz\\n")
    assert r.returncode == 0, r.stderr
    assert acalls.splitlines() == ["emu avd hostmicon"]
    lines = pcalls.splitlines()
    for want in ("set-source-mute tlwu_clip.monitor 0", "move-source-output 7 tlwu_clip.monitor",
                 "set-source-output-mute 7 0", "set-source-output-volume 7 100%"):
        assert want in lines, pcalls
    out = (tmp_path / "pulse" / "source-outputs.txt").read_text(encoding="utf-8")
    assert "hostmicon: OK" in out and "record streams: 1" in out
    assert "Source Output #7 Source: 3" in out and 'qemu-system-x86_64' in out
    assert "source 3 tlwu_clip.monitor IDLE" in out


def test_route_without_streams_or_pactl_never_fails(tmp_path):
    r, pcalls, _ = _route(tmp_path, "")
    assert r.returncode == 0, r.stderr
    assert "move-source-output" not in pcalls
    assert "record streams: 0" in (tmp_path / "pulse" / "source-outputs.txt").read_text(encoding="utf-8")
    # No stub pactl and no ADB: the system pactl (if any) finds no server, and route still exits 0.
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "TLWU_PULSE_DIR": str(tmp_path / "p2"),
           "PULSE_SERVER": f"unix:{tmp_path}/no-such-socket"}
    r2 = subprocess.run([BASH, str(ROOT / "mobile/tool/host_mic.sh"), "route"], env=env, capture_output=True,
                        text=True, timeout=30)
    assert r2.returncode == 0, r2.stderr


def test_serve_routes_before_and_after_mic_go(tmp_path):
    r, calls = _mic_step(tmp_path, FAKE_TEST)
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls.splitlines().count("emu avd hostmicon") == 2, calls


def test_summary_lists_pulse_record_streams(tmp_path):
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    (tmp_path / "rt").mkdir(); (tmp_path / "pulse").mkdir()
    (tmp_path / "pulse" / "source-outputs.txt").write_text("record streams: 1\n", encoding="utf-8")
    r = subprocess.run([BASH, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", _step("Job summary")["run"]],
                       cwd=tmp_path, env=_env(tmp_path, bin_dir), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "- pulse: record streams: 1" in (tmp_path / "summary.md").read_text(encoding="utf-8")
