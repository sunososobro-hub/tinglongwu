"""#602: scripts/bench/tier2_report.py turns a tlwu_eval --langs run into one chrF row per tier-2 language."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("tier2_report_602", ROOT / "scripts" / "bench" / "tier2_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


t2 = _load()


def _rep(langs=t2.TIER2):
    return {"languages": [{"pair": f"{x}->zh", "clips": [{}] * 20, "chrf": 30.0 + i, "error_rate": 0.1,
                           "mt_median_s": 0.4} for i, x in enumerate(langs)]}


def test_one_row_per_tier2_language(tmp_path):
    src = tmp_path / "run.json"
    src.write_text(json.dumps(_rep()), encoding="utf-8")
    out = tmp_path / "tier2_report.json"
    assert t2.main([str(src), "--model", "m", "--engine", "e", "--device", "cpu", "--out", str(out)]) == 0
    rep = json.loads(out.read_text("utf-8"))
    assert list(rep["pairs"]) == [f"{x}->zh" for x in t2.TIER2]
    assert rep["pairs"]["th->zh"] == {"clips": 20, "chrf": 31.0, "asr_error_rate": 0.1, "mt_median_s": 0.4}
    assert rep["run"]["model"] == "m" and rep["run"]["pins"] == "eval/tier2_pins.json"


def test_missing_language_fails(tmp_path, capsys):
    src = tmp_path / "run.json"
    src.write_text(json.dumps(_rep(("vi", "th"))), encoding="utf-8")
    assert t2.main([str(src), "--model", "m", "--engine", "e", "--device", "cpu", "--out", str(tmp_path / "o.json")]) == 1
    assert "id->zh" in capsys.readouterr().err


def test_committed_report_has_a_chrf_row_per_language():
    rep = json.loads((ROOT / "eval" / "tier2_report.json").read_text("utf-8"))
    assert set(rep["pairs"]) == {f"{x}->zh" for x in t2.TIER2}
    for r in rep["pairs"].values():
        assert r["clips"] >= 20 and isinstance(r["chrf"], (int, float))
