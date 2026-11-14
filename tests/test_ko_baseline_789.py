"""#789: the Korean (ko -> zh-TW) eval slice has pinned clips and a recorded chrF baseline the gate accepts."""
import json
import re
from pathlib import Path

import pytest

from tlwu_eval import gate

ROOT = Path(__file__).resolve().parents[1]
PINS = ROOT / "eval" / "ko_pins.json"
BASELINE = ROOT / "eval" / "baseline_ko.json"


def _report(chrf, median):
    return {"languages": [{"pair": "ko->zh", "clips": [{}] * 5, "chrf": chrf, "mt_median_s": median}]}


def test_ko_pins_has_five_fleurs_ko_clips_with_sha256():
    pin = json.loads(PINS.read_text("utf-8"))
    assert pin["dataset"] == "google/fleurs"
    assert re.fullmatch(r"[0-9a-f]{40}", pin["revision"])
    assert pin["configs"] == {"ko": "ko_kr"}
    clips = pin["clips"]
    assert len(clips) == 5
    assert len({c["id"] for c in clips}) == 5
    for c in clips:
        assert c["lang"] == "ko"
        assert re.fullmatch(r"[0-9a-f]{64}", c["sha256"])
        assert isinstance(c["flores_id"], int)
        assert 0 < c["seconds"] <= 15.0


def test_ko_baseline_passes_the_gate_schema_and_has_the_measurement_context():
    b = gate.validate_baseline(gate.load_json(BASELINE, "baseline"))
    assert set(b["pairs"]) == {"ko->zh"}
    assert b["clips"] == 5
    assert b["model"] == "whisper small" and "Hy-MT2 1.8B" in b["engine"] and b["device"] == "cpu"
    assert b["machine"]


@pytest.mark.parametrize("chrf, median, result", [
    (21.17, 1.668, "PASS"),  # the second measured run
    (21.0, 1.793, "PASS"),   # the first measured run (the recorded lowest chrF)
    (18.5, 1.7, "FAIL"),     # chrF dropped more than the 2.0 margin
    (21.0, 3.0, "FAIL"),     # MT median over budget
])
def test_gate_compares_a_ko_report_against_the_recorded_baseline(chrf, median, result):
    b = gate.validate_baseline(gate.load_json(BASELINE, "baseline"))
    (row,) = gate.compare(_report(chrf, median), b)
    assert row["pair"] == "ko->zh"
    assert row["result"] == result
