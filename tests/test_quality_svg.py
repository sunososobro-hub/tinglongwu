"""#1520: scripts/bench/quality_svg.py renders the faces grid and the Google cards from the real 6x6 data."""
import json
import subprocess
import sys
import xml.dom.minidom
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "scripts" / "bench" / "data"
MATRIX = DATA / "matrix_6x6_44099762645.json"
GOOGLE = DATA / "google_refs.json"


def _svg() -> str:
    r = subprocess.run([sys.executable, str(ROOT / "scripts/bench/quality_svg.py"), str(MATRIX), str(GOOGLE)],
                       capture_output=True, text=True, encoding="utf-8", check=True)
    return r.stdout


def test_quality_svg_face_counts_and_google_cards():
    svg = _svg()
    xml.dom.minidom.parseString(svg.encode("utf-8"))  # well-formed
    assert "<title>" in svg and "<desc>" in svg and 'width="900"' in svg
    assert "😀 good  17" in svg and "🙂 fine  8" in svg and "😕 weak  5" in svg
    for line in ("RU → 繁: 99 / 100", "EN → 繁: 99 / 100", "RU → EN: 98 / 100"):
        assert line in svg
    assert "Not measured yet" in svg and "GPU: speed not measured yet" in svg


def test_quality_svg_uses_matrix_lights_thresholds():
    sys.path.insert(0, str(ROOT / "scripts" / "bench"))
    import matrix_lights
    import quality_svg
    d = json.loads(MATRIX.read_text("utf-8"))
    assert quality_svg.GREEN_MIN == matrix_lights.GREEN_MIN and quality_svg.YELLOW_MIN == matrix_lights.YELLOW_MIN
    assert quality_svg.render(d, {}).count("<rect") >= 30


def test_committed_svg_matches_generator():
    assert (ROOT / "docs" / "assets" / "quality.svg").read_text("utf-8") == _svg()
