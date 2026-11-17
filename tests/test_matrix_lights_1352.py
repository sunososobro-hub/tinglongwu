import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("matrix_lights", ROOT / "scripts" / "bench" / "matrix_lights.py")
ml = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ml)


@pytest.mark.parametrize("c,want", [(0.8691, "red"), (0.8699, "yellow"), (0.8794, "yellow"), (0.8796, "green")])
def test_thresholds_round_to_three_decimals(c, want):
    assert ml.light(c) == want


def test_exact_threshold_values():
    assert ml.light(0.869) == "red"
    assert ml.light(0.870) == "yellow"
    assert ml.light(0.879) == "yellow"
    assert ml.light(0.880) == "green"


def _s(a, b, c):
    return {"pair": f"{a}->{b}", "COMET": c, "chrF": 30.0, "n": 200}


def test_cell_with_and_without_google_reference():
    sums = [_s("rus_Cyrl", "eng_Latn", 0.854), _s("eng_Latn", "rus_Cyrl", 0.889), _s("zho_Hans", "zho_Hant", 0.92)]
    out = ml.render(sums, {"rus_Cyrl->eng_Latn": {"COMET": 0.883, "n": 50}})
    assert "🔴 0.854<br>G 0.883 (gap +0.029)" in out
    assert "🟢 0.889<br>G -" in out
    assert "(script conversion)" in out
    assert "1 green / 0 yellow / 1 red" not in out
    assert "2 green / 0 yellow / 1 red" in out
    assert "largest gap to Google: ru -> en +0.029" in out


def test_cli_on_committed_run_prints_counts():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/bench/matrix_lights.py"),
                        str(ROOT / "scripts/bench/data/matrix_6x6_43893644257.json")],
                       capture_output=True, text=True, check=True)
    assert "17 green / 7 yellow / 6 red" in r.stdout
    assert "largest gap to Google: ru -> en +0.029" in r.stdout


def test_cli_reads_a_summary_directory(tmp_path):
    (tmp_path / "a.summary.json").write_text(json.dumps(_s("eng_Latn", "rus_Cyrl", 0.9)))
    r = subprocess.run([sys.executable, str(ROOT / "scripts/bench/matrix_lights.py"), str(tmp_path)],
                       capture_output=True, text=True, check=True)
    assert "1 green / 0 yellow / 0 red" in r.stdout
