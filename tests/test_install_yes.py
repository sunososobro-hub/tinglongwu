"""#364: `TLWU_YES=1` / `--yes` makes scripts/install.sh fully unattended (apt yes, Kokoro, default ASR/MT models).

Runs the real install.sh with stubbed outside effects (id, dpkg, sudo, git, the venv python, linux_kokoro.sh) and
stdin = /dev/null, so any `read` would get EOF and the script would stop at the apt prompt.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="needs bash")


def _stub(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)


def _run(tmp_path: Path, args=(), extra_env=None, dpkg_ok=False):
    log = tmp_path / "calls.log"
    fake = tmp_path / "bin"
    d = tmp_path / "tlwu"
    (d / ".git").mkdir(parents=True)
    # python3.12 for the installer's version check
    code = "import sys; sys.version_info = (3, 12, 0); exec(sys.argv[-1])"
    _stub(fake / "python3.12", f'if [ "$1" = "--version" ]; then echo "Python 3.12.0"; exit 0; fi\n'
                               f'exec "{sys.executable}" -c "{code}" "$2"')
    _stub(fake / "python3", "exit 1")
    _stub(fake / "id", "echo 1000")
    _stub(fake / "dpkg", "exit 0" if dpkg_ok else "exit 1")  # exit 1 = audio libs missing -> apt step
    _stub(fake / "sudo", 'echo "sudo $*" >> "$TLWU_LOG"')
    _stub(fake / "apt-get", "exit 0")
    _stub(fake / "curl", "exit 0")
    _stub(fake / "git", "exit 0")
    _stub(d / "scripts" / "linux_kokoro.sh", 'echo "kokoro" >> "$TLWU_LOG"')
    # the venv python: setup is logged, the tier probe runs the real tlwu_app.tiers
    _stub(d / ".venv" / "bin" / "python",
          f'case "$1" in\n -m) if [ "$2" = "tinglongwu_setup" ]; then echo "setup $*" >> "$TLWU_LOG"; fi; exit 0 ;;\n'
          f' -c) PYTHONPATH="{ROOT}" exec "{sys.executable}" -c "$2" ;;\nesac\nexit 0')
    env = {"PATH": f"{fake}:/usr/bin:/bin", "HOME": str(tmp_path), "TLWU_DIR": str(d), "TLWU_UI_LANG": "en",
           "TLWU_LOG": str(log), **(extra_env or {})}
    r = subprocess.run(["bash", str(ROOT / "scripts" / "install.sh"), *args], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, env=env, timeout=120)
    return r, (log.read_text() if log.exists() else "")


def test_tlwu_yes_is_unattended_and_downloads_default_models(tmp_path):
    r, calls = _run(tmp_path, extra_env={"TLWU_YES": "1"})
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y" in calls  # apt answered yes without a prompt
    assert "kokoro" in calls
    setup = [ln for ln in calls.splitlines() if ln.startswith("setup ")]
    assert len(setup) == 1
    assert "--whisper" in setup[0] and "--hymt" in setup[0] and "--packs zh,en" in setup[0]
    assert "vosk" not in setup[0].lower()  # the Russian voice stays opt-in
    assert "first start needs no download" in out


def test_yes_flag_is_the_same_as_env(tmp_path):
    r, calls = _run(tmp_path, args=["--yes"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "setup -m tinglongwu_setup --whisper" in calls


def test_without_yes_apt_still_asks_and_no_models(tmp_path):
    r, calls = _run(tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out  # stdin is /dev/null: the y/N prompt gets EOF = default no
    assert "Not installed" in out
    assert "sudo" not in calls and "setup" not in calls


def test_without_yes_models_are_not_downloaded(tmp_path):
    r, calls = _run(tmp_path, dpkg_ok=True)  # nothing missing: no prompt at all
    assert r.returncode == 0, r.stdout + r.stderr
    assert "kokoro" in calls and "setup" not in calls
    assert "Models are NOT downloaded yet" in r.stdout


def test_yes_with_skip_models_downloads_nothing(tmp_path):
    r, calls = _run(tmp_path, extra_env={"TLWU_YES": "1", "TLWU_SKIP_MODELS": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "kokoro" not in calls and "setup" not in calls
