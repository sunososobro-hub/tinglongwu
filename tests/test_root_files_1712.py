"""#1712: the repo root holds only what tools need there; everything else lives in a folder.

A new root file must either go into a folder or be added to ROOT_ALLOWLIST with the reason.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ROOT_ALLOWLIST = {
    ".all-contributorsrc", ".coderabbit.yaml", ".gitattributes", ".gitignore",  # read at root by their tools
    "CLAUDE.md", "LICENSE", "README.md",  # agents / GitHub look at the root
    "pyproject.toml",  # pip install -e .
    "codemagic.yaml",  # Codemagic reads it only from the repo root
    "install.bat",  # double-click entry of the source ZIP and the wizard's Repair shortcut (as small as a stub)
    "install.ps1",  # stub -> scripts/install.ps1 (older release bootstraps, run_install.ps1)
    "update.bat",  # stub -> scripts/update.bat (older installs double-click the root copy)
    "requirements.txt",  # stub -> requirements/requirements.txt (older update.py / auto_update.sh)
}


def _root_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return {p for p in out.splitlines() if p and "/" not in p}


def test_no_new_root_files():
    extra = _root_files() - ROOT_ALLOWLIST
    assert not extra, f"new root files {sorted(extra)}: move them into a folder (or allowlist them with a reason, #1712)"


def test_root_stubs_forward_to_the_moved_files():
    assert (ROOT / "scripts" / "install.ps1").is_file() and (ROOT / "scripts" / "update.bat").is_file()
    assert "'scripts') 'install.ps1') @args" in (ROOT / "install.ps1").read_text("ascii")
    assert "scripts\\update.bat\" %*" in (ROOT / "update.bat").read_text("ascii")
    assert "scripts\\install.ps1\"" in (ROOT / "install.bat").read_text("utf-8")
    assert "-r requirements/requirements.txt" in (ROOT / "requirements.txt").read_text("ascii")
    for name in ("requirements.txt", "requirements-gpu.txt", "requirements-llama-cpu.txt", "requirements-llama-cuda.txt",
                 "requirements-speaker.txt", "requirements.lock.txt"):
        assert (ROOT / "requirements" / name).is_file(), name
