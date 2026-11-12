"""Guard: nothing in the repo may open a visible console window (CI runs on the owner's desktop)."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# built from parts so this file does not match its own scan
BANNED = "CREATE_NEW_" + "CONSOLE"


def _py_files():
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True)
    if out.returncode == 0 and out.stdout.strip():
        return [ROOT / p for p in out.stdout.splitlines()]
    return [p for p in ROOT.rglob("*.py") if ".git" not in p.parts and "venv" not in p.parts]


def test_no_create_new_console():
    hits = [str(p.relative_to(ROOT)) for p in _py_files() if p.exists() and BANNED in p.read_text("utf-8", "replace")]
    assert not hits, f"{BANNED} pops a visible window; use subprocess.CREATE_NO_WINDOW instead (it also gives the " f"child its own console, so CTRL_C_EVENT still works): {hits}"
