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
