"""#8: macOS end-to-end without a Mac.

Runs the real scripts/mac_install.sh and scripts/linux_menu.sh under bash on a fake Darwin: `uname`, `sw_vers`,
`brew`, `curl`, `id` and the Python interpreters are stubs on PATH that log their argv; git is real (a local repo
stands in for GitHub). This covers what the cloud macOS install-test skips (BlackHole casks, Kokoro download, model
setup, the BlackHole self-check) and what listen / speak / meeting modes launch on a Mac. What still needs a real
Mac (CoreAudio sees BlackHole after reboot, Audio MIDI Setup routing, mic permission) is the short checklist in #8.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash") or not shutil.which("git"),
                                reason="needs bash + git")

KOKORO_SIZES = {"kokoro-v1.0.int8.onnx": 92361271, "voices-v1.0.bin": 28214398}

COMMON_STUBS = {
    "uname": 'case "$1" in -m) echo arm64;; *) echo Darwin;; esac',
    "sw_vers": "echo 14.5",
    "sysctl": "echo 0",
    "id": 'if [ "$1" = "-u" ]; then echo 501; else /usr/bin/id "$@"; fi',
}

# The interpreter stub for brew's python3.12 and every venv created from it (main .venv, ~/tts-kokoro).
PYSTUB = r'''echo "py $0 $*" >> "$LOG"
case "$1" in
  --version) echo "Python 3.12.7" ;;
  -m)
    case "$2" in
      venv) mkdir -p "$3/bin"; cp "$STUBS/pystub" "$3/bin/python"; cp "$STUBS/tlwu-setup" "$3/bin/tlwu-setup" ;;
      tinglongwu.reverse.proto)
        case "$3" in
          --list-speakers) printf '%s' "${FAKE_SPK:-}" | tr '|' '\n' ;;
          --list-mics) printf '%s' "${FAKE_MIC:-}" | tr '|' '\n' ;;
        esac ;;
    esac ;;
esac
exit 0
'''

INSTALL_STUBS = {
    "pystub": PYSTUB,
    "tlwu-setup": 'echo "tlwu-setup $*" >> "$LOG"',
    # brew: state = marker files; --prefix python@3.12 points at a dir whose bin/python3.12 is the stub
    "brew": r'''echo "brew $*" >> "$LOG"
case "$1" in
  shellenv) ;;
  --prefix) echo "$STUBS/brewpy" ;;
  list) [ -e "$BREWSTATE/$3" ] ;;
  install) if [ "$2" = "--cask" ]; then touch "$BREWSTATE/$3"; else touch "$BREWSTATE/$2"; fi ;;
esac''',
    # curl: the github.com reachability probe, and model downloads that produce a file of the published size
    "curl": r'''echo "curl $*" >> "$LOG"
out=""; url=""
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift ;; http*) url="$1" ;; esac; shift; done
[ -z "$out" ] || [ "$out" = /dev/null ] && exit 0
case "$url" in
  */kokoro-v1.0.int8.onnx) truncate -s 92361271 "$out" ;;
  */voices-v1.0.bin) truncate -s 28214398 "$out" ;;
  *) exit 22 ;;
esac''',
}


def write_stubs(d: Path, stubs: dict):
    d.mkdir(parents=True, exist_ok=True)
    for name, body in stubs.items():
        p = d / name
        p.write_text("#!/bin/bash\n" + body + "\n")
        p.chmod(0o755)


def git(cwd, *a):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=str(cwd), check=True,
                   capture_output=True)


@pytest.fixture
def mac(tmp_path):
    """A fake Mac: HOME, stub PATH, and a local 'GitHub' repo with branch main + tag v9.9.9."""
    stubs, home, state, upstream = tmp_path / "stubs", tmp_path / "home", tmp_path / "brewstate", tmp_path / "up"
    write_stubs(stubs, {**COMMON_STUBS, **INSTALL_STUBS})
    (stubs / "brewpy" / "bin").mkdir(parents=True)
    shutil.copy(stubs / "pystub", stubs / "brewpy" / "bin" / "python3.12")
    home.mkdir()
    state.mkdir()
    upstream.mkdir()
    git(upstream, "init", "-q", "-b", "main")
    (upstream / "README.md").write_text("x\n")
    (upstream / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "tlwu_common.sh", upstream / "scripts")  # sourced by the self-test step (#99)
    git(upstream, "add", ".")
    git(upstream, "commit", "-qm", "init")
    git(upstream, "tag", "v9.9.9")
    (upstream / "after-release.txt").write_text("not in the release\n")
    git(upstream, "add", ".")
    git(upstream, "commit", "-qm", "post-release")
    log = tmp_path / "calls.log"
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(home), "LOG": str(log), "STUBS": str(stubs),
           "BREWSTATE": str(state), "TLWU_REPO": str(upstream), "LANG": "C.UTF-8",
           "FAKE_SPK": "MacBook Pro Speakers|BlackHole 2ch|BlackHole 16ch",
           "FAKE_MIC": "MacBook Pro Microphone|BlackHole 2ch|BlackHole 16ch"}

    def run(script=ROOT / "scripts" / "mac_install.sh", **extra):
        r = subprocess.run(["bash", str(script)], env={**env, **extra}, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=120)
        calls = log.read_text() if log.exists() else ""
        log.unlink(missing_ok=True)
        return r, calls

    run.home, run.state, run.tmp = home, state, tmp_path
    return run


def test_mac_install_full_flow_then_rerun_is_a_repair(mac):
    r, calls = mac(TLWU_BRANCH="main")
    assert r.returncode == 0, r.stdout + r.stderr
    home = mac.home
    # BlackHole 2ch (listen) + 16ch (virtual mic) casks, python + git formulae
    for c in ("brew install python@3.12", "brew install git", "brew install --cask blackhole-2ch",
              "brew install --cask blackhole-16ch"):
        assert c in calls, c
    # repo cloned, venv built from brew's python 3.12, package installed
    assert (home / "tinglongwu" / "after-release.txt").is_file()
    assert "-m pip install -e ." in calls
    # Kokoro voice: own venv with the pinned package, both model files at their published size
    assert "tts-kokoro/bin/python -m pip install kokoro-onnx==0.6.1" in calls
    for name, size in KOKORO_SIZES.items():
        assert (home / "models" / "kokoro" / name).stat().st_size == size
    # default models: English listening + zh<->en speaking
    assert "tlwu-setup --whisper small --pair en-zh --pair zh-en" in calls
    assert "tlwu-setup --selftest --model small" in calls  # last step: load the models, translate a test line (#99)
    # launcher + PATH line for zsh (the macOS default shell)
    menu = home / ".local" / "bin" / "tlwu-menu"
    assert os.access(menu, os.X_OK) and "scripts/linux_menu.sh" in menu.read_text()
    for rc in (".zprofile", ".zshrc"):
        assert (home / rc).read_text().count("added by tinglongWu") == 1
    # BlackHole self-check saw both devices: no warnings
    assert "BlackHole 2ch: 已偵測到" in r.stdout and "BlackHole 16ch: 已偵測到" in r.stdout
    assert "[注意事項彙整]" not in r.stdout

    # re-run = repair: nothing reinstalled, nothing re-downloaded, PATH line not duplicated
    r2, calls2 = mac(TLWU_BRANCH="main")
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "brew install" not in calls2
    assert "kokoro-v1.0.int8.onnx" not in calls2 and "voices-v1.0.bin" not in calls2
    assert "-m venv .venv" not in calls2
    for rc in (".zprofile", ".zshrc"):
        assert (home / rc).read_text().count("added by tinglongWu") == 1


def test_mac_install_release_script_installs_its_own_tag(mac):
    """The release asset has __TLWU_REF__ replaced by the tag: it must check out that tag, not main's head."""
    rel = mac.tmp / "mac_install.sh"
    rel.write_text((ROOT / "scripts" / "mac_install.sh").read_text("utf-8").replace("__TLWU_REF__", "v9.9.9"),
                   "utf-8")
    r, _ = mac(script=rel)
    assert r.returncode == 0, r.stdout + r.stderr
    d = mac.home / "tinglongwu"
    assert (d / "README.md").is_file() and not (d / "after-release.txt").exists()
    # re-run of the same release on an existing checkout stays on the tag
    r2, _ = mac(script=rel)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert not (d / "after-release.txt").exists()


def test_mac_install_warns_until_blackhole_is_visible(mac):
    """Fresh BlackHole is invisible to CoreAudio until a reboot: install still succeeds, with a reboot hint."""
    r, _ = mac(TLWU_BRANCH="main", FAKE_SPK="MacBook Pro Speakers", FAKE_MIC="MacBook Pro Microphone")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[注意事項彙整]" in r.stdout
    assert "BlackHole 2ch" in r.stderr and "BlackHole 16ch" in r.stderr and "重新開機" in r.stderr


def test_mac_install_refuses_root_and_non_mac(mac, tmp_path):
    write_stubs(tmp_path / "stubs", {"id": 'echo 0'})
    r, _ = mac(TLWU_BRANCH="main")
    assert r.returncode == 1 and "sudo" in r.stderr
    write_stubs(tmp_path / "stubs", {**COMMON_STUBS, "uname": "echo Linux"})
    r, _ = mac(TLWU_BRANCH="main")
    assert r.returncode == 1 and "Mac" in r.stderr


# ---------------------------------------------------------------- the menu (tlwu-menu) on a Mac

MENU_STUBS = {
    "uname": COMMON_STUBS["uname"],
    "tlwu": 'echo "tlwu $*" >> "$LOG"',
    "tlwu-setup": 'echo "tlwu-setup $*" >> "$LOG"',
    # python: the app's own entry points are logged; helper one-liners (have_pairs ...) run on the real interpreter
    "python": r'''if [ "$1" = "-m" ]; then
  case "$2" in
    tinglongwu.reverse.proto)
      echo "proto ${*:3}" >> "$LOG"
      if [ "$3" = "--list-speakers" ]; then
        printf '%s\n' "MacBook Pro Speakers" "AirPods Pro" "BlackHole 2ch" "BlackHole 16ch"
      else sleep 0.3; fi   # a session outlives the (stub) listener started before it, as when tlwu exits early
      exit 0 ;;
    tinglongwu.netreport) exit 1 ;;
  esac
fi
exec "$REALPY" "$@"''',
}


@pytest.fixture
def menu(tmp_path):
    repo, stubs, models = tmp_path / "repo", tmp_path / "stubs", tmp_path / "models"
    shutil.copytree(ROOT / "scripts", repo / "scripts")
    write_stubs(stubs, MENU_STUBS)
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "activate").write_text(f'export PATH="{stubs}:$PATH"\n')
    for p in ("en-zh", "zh-en"):   # what mac_install.sh downloads
        (models / "opus" / p).mkdir(parents=True)
    kokoro = tmp_path / "home" / "models" / "kokoro"   # what mac_install.sh installs (ensure_kokoro checks all 3)
    kokoro.mkdir(parents=True)
    for name in ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin"):
        (kokoro / name).write_bytes(b"x")
    kpy = tmp_path / "home" / "tts-kokoro" / "bin" / "python"
    kpy.parent.mkdir(parents=True)
    kpy.write_text("#!/bin/sh\nexit 0\n")
    kpy.chmod(0o755)
    log = tmp_path / "calls.log"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"), "LOG": str(log), "REALPY": sys.executable,
           "PYTHONPATH": str(ROOT), "TLWU_MODELS": str(models), "TLWU_NO_UPDATE": "1", "TLWU_UI_LANG": "en",
           "XDG_CONFIG_HOME": str(tmp_path / "cfg"), "TMPDIR": str(tmp_path), "LANG": "C.UTF-8"}

    def run(keys: str):
        r = subprocess.run(["bash", str(repo / "scripts" / "linux_menu.sh")], env=env, input=keys,
                           capture_output=True, text=True, timeout=60)
        calls = log.read_text() if log.exists() else ""
        log.unlink(missing_ok=True)
        return r, calls.splitlines()

    return run


def test_mac_menu_listen_mode_english_without_russian_voice(menu):
    r, calls = menu("3\nn\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls == ["tlwu --lang en"]
    assert not any(c.startswith("tlwu-setup") for c in calls)   # models from the installer are enough


def test_mac_menu_speak_mode_uses_blackhole_16ch_and_kokoro(menu):
    r, calls = menu("2\n1\nn\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "BlackHole 16ch" in r.stdout
    [c] = calls
    assert c.startswith("proto --mic") and "--to en" in c and "--tts-engine kokoro" in c
    assert "--device" not in c and "--no-vmic" not in c   # proto picks BlackHole 16ch itself


def test_mac_menu_speak_mode_to_headphones_skips_the_virtual_mic(menu):
    r, calls = menu("2\n3\nn\nAirPods Pro\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls[0] == "proto --list-speakers"
    assert calls[1].endswith("--no-vmic --device AirPods Pro")


def test_mac_menu_meeting_mode_listens_and_speaks(menu):
    """Also: ending the session after the background listener already exited is a clean exit (the EXIT trap's
    kill of a finished pid used to turn it into exit 1 under set -e)."""
    r, calls = menu("1\nn\nn\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "BlackHole 16ch" in r.stdout
    assert "tlwu --lang en" in calls
    [rev] = [c for c in calls if c.startswith("proto")]
    assert "--to en" in rev and "--tts-engine kokoro" in rev and "--device" not in rev
