"""#1895 (part 3a of #1816): shared/mt_spec.json is the one source of the MT prompt text; desktop hymt.py reads it and
the phone's generated Dart is derived from it."""
import json
import subprocess
import sys
from pathlib import Path

from tinglongwu import hymt

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "shared" / "mt_spec.json"


def test_generator_check_passes_and_is_deterministic():
    """The committed desktop copy and mobile/lib/logic/mt_spec.g.dart equal what the generator writes from the JSON."""
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_mt_spec.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_generator_detects_stale_derived_file(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import gen_mt_spec
    finally:
        sys.path.pop(0)
    stale = tmp_path / "mt_spec.g.dart"
    stale.write_text("old", encoding="utf-8")
    monkeypatch.setattr(gen_mt_spec, "DART", stale)
    monkeypatch.setattr(sys, "argv", ["gen_mt_spec.py", "--check"])
    assert gen_mt_spec.main() == 1
    monkeypatch.setattr(sys, "argv", ["gen_mt_spec.py"])
    assert gen_mt_spec.main() == 0 and stale.read_text(encoding="utf-8") == gen_mt_spec.render_dart(
        json.loads(SPEC.read_text(encoding="utf-8")))


def test_generated_dart_carries_the_phone_tables():
    dart = (ROOT / "mobile" / "lib" / "logic" / "mt_spec.g.dart").read_text(encoding="utf-8")
    assert "'yue': '粤语'" in dart and "'hi': '印地语'" in dart  # #1896: both sides carry yue and hi
    assert hymt.ZH_NAMES["hi"] == "印地语" and hymt.ZH_NAMES["yue"] == "粤语"


def test_desktop_prompts_come_from_the_spec(monkeypatch):
    monkeypatch.setitem(hymt._TPL, "zh", "ZH[{target}|{text}]")
    assert hymt.build_prompt("hi {text}", "en", "zh") == "ZH[中文|hi {text}]"  # a value holding {..} is not refilled
    assert hymt.build_prompt("x", "en", "ru").startswith("Translate the following text into Russian.")

