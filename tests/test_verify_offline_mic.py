import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "verify_offline.py"


def test_build_cmd_mic_and_file():
    sys.path.insert(0, str(SCRIPT.parent))
    import verify_offline as v
    assert v.build_cmd(None, True, ["--x"])[-2:] == ["--mic", "--x"]
    assert v.build_cmd("a.wav", False, [])[-2:] == ["--file", "a.wav"]


def test_requires_a_source():
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 2 and "--mic" in r.stderr


def test_mic_child_exiting_early_fails():
    root = SCRIPT.parent.parent
    r = subprocess.run([sys.executable, str(SCRIPT), "--mic", "--seconds", "60", "--", "--bogus-flag"],
                       capture_output=True, text=True, cwd=root, timeout=120)
    assert r.returncode == 1 and "exited early" in r.stdout
