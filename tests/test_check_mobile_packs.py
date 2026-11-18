"""#1510: scripts/check_mobile_packs.py exit codes with a fixture asset list."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_mobile_packs.py"
ALL = ["tinglongwu-mobile-arm64.apk", "tlwu-mobile-models.zip",
       "tlwu-voice-en.zip", "tlwu-voice-ru.zip", "tlwu-mobile-lang-ru.zip"]


def run(tmp_path, names):
    f = tmp_path / "assets.json"
    f.write_text(json.dumps({"assets": [{"name": n} for n in names]}))
    return subprocess.run([sys.executable, str(SCRIPT), "--assets-json", str(f)],
                          capture_output=True, text=True)


def test_all_present_exits_0(tmp_path):
    r = run(tmp_path, ALL)
    assert r.returncode == 0, r.stdout


def test_missing_pack_exits_1_and_names_it(tmp_path):
    r = run(tmp_path, [n for n in ALL if n != "tlwu-voice-ru.zip"])
    assert r.returncode == 1
    assert "tlwu-voice-ru.zip" in r.stdout
    assert "tlwu-voice-en.zip" not in r.stdout
