"""#1116: the release-assets job pre-installs every system package install.sh asks for, so the release's install.sh
(run with </dev/null, which answers its apt y/N prompt with EOF = no) does not stop at "Missing system packages".

Runs the real scripts/install.sh on a fake Ubuntu 24.04 runner (python3 is 3.12) where a package counts as installed
only if the workflow step's `apt-get install` line names it.
"""
from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "install-test.yml"
pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="needs bash")


def _apt_packages_of_release_assets_job(wf_path: Path = WF) -> set[str]:
    wf = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
    pkgs: set[str] = set()
    for step in wf["jobs"]["release-assets"]["steps"]:
        if "Linux" not in str(step.get("if", "")):
            continue
        for cmd in str(step.get("run", "")).split("&&"):
            words = shlex.split(cmd)
            if "apt-get" in words and "install" in words:
                pkgs |= {w for w in words[words.index("install") + 1:] if not w.startswith("-")}
    return pkgs


def _stub(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)


def _run_installer(tmp_path: Path, installed: set[str]):
    fake = tmp_path / "bin"
    d = tmp_path / "tlwu"
    (d / ".git").mkdir(parents=True)
    tk = 0 if "python3-tk" in installed else 1
    code = "import sys; sys.version_info = (3, 12, 0); exec(sys.argv[-1])"
    py = (f'if [ "$1" = "--version" ]; then echo "Python 3.12.0"; exit 0; fi\n'
          f'case "$2" in *"import tkinter"*) exit {tk} ;; esac\n'
          f'exec "{sys.executable}" -c "{code}" "$2"')
    _stub(fake / "python3", py)  # Ubuntu 24.04: the system python3 is 3.12
    _stub(fake / "python3.12", py)
    _stub(fake / "dpkg", 'case " ' + " ".join(sorted(installed)) + ' " in *" $2 "*) exit 0 ;; esac\nexit 1')
    _stub(fake / "id", "echo 1000")
    _stub(fake / "sudo", "exit 0")
    _stub(fake / "apt-get", "exit 0")
    _stub(fake / "curl", "exit 0")
    _stub(fake / "git", "exit 0")
    _stub(d / "scripts" / "linux_kokoro.sh", "exit 0")
    _stub(d / ".venv" / "bin" / "python",
          f'case "$1" in\n -m) exit 0 ;;\n'
          f' -c) case "$2" in *"import tkinter"*) exit {tk} ;; *"import llama_cpp"*) exit 0 ;; esac\n'
          f'     PYTHONPATH="{ROOT}" exec "{sys.executable}" -c "$2" ;;\nesac\nexit 0')
    env = {"PATH": f"{fake}:/usr/bin:/bin", "HOME": str(tmp_path), "TLWU_DIR": str(d), "TLWU_UI_LANG": "en",
           "TLWU_SKIP_MODELS": "1", "TLWU_SKIP_BLACKHOLE": "1"}
    return subprocess.run(["bash", str(ROOT / "scripts" / "install.sh")], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, env=env, timeout=120)


def test_release_assets_linux_packages_satisfy_install_sh(tmp_path):
    pkgs = _apt_packages_of_release_assets_job()
    assert pkgs, "release-assets job has no Linux apt-get install step"
    r = _run_installer(tmp_path, pkgs)
    out = r.stdout + r.stderr
    assert "Missing system packages" not in out, out
    assert "Not installed" not in out, out


def test_without_tk_the_installer_stops(tmp_path):
    # the pre-#1116 package list (audio libs only): install.sh asks for python3-tk, gets EOF and exits 1
    r = _run_installer(tmp_path, {"libsndfile1", "libpulse0"})
    out = r.stdout + r.stderr
    assert r.returncode != 0
    assert "Missing system packages" in out and "python3-tk" in out, out
