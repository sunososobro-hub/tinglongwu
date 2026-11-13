"""#610: scripts/topic_bakeoff.py scoring, model pick, table, and the harness with a fake summarizer."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("topic_bakeoff", ROOT / "scripts" / "topic_bakeoff.py")
tb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tb)


def test_help_runs():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "topic_bakeoff.py"), "--help"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0 and "--model" in r.stdout


def test_rubric_score():
    assert tb.rubric_score("Budget: Two Backend engineers by march, Q2", ["two backend", "March", "Q2", "Monday"]) == 0.75
    assert tb.rubric_score("", ["a"]) == 0.0


def test_pick_smallest_within_10pct():
    rows = [{"model": "big", "load_ram_mb": 3000, "subtitle_p50_delta_pct": 2.0},
            {"model": "mid", "load_ram_mb": 2000, "subtitle_p50_delta_pct": 9.9},
            {"model": "small", "load_ram_mb": 1200, "subtitle_p50_delta_pct": 25.0}]
    assert tb.pick_model(rows)["model"] == "mid"
    assert tb.pick_model([rows[2]]) is None


def test_bench_model_with_fake_summarizer():
    def summarize(lang, text):  # echoes the input so every rubric fact is present
        return text

    def probe(stop, **kw):
        return 0.010 if stop is None else 0.0105

    row = tb.bench_model("fake", summarize, 123.0, probe=probe)
    assert row["quality"] == {"zh": 1.0, "en": 1.0, "ja": 1.0}
    assert abs(row["subtitle_p50_delta_pct"] - 5.0) < 1e-6 and row["load_ram_mb"] == 123.0


def test_table_and_results_merge(tmp_path, capsys):
    row = {"model": "Qwen3-1.7B", "load_ram_mb": 1500, "sec_per_topic": 12.3, "quality": {"zh": .8, "en": 1, "ja": .5},
           "subtitle_p50_delta_pct": 4.0}
    for n in (4, 8):
        (tmp_path / f"{n}.json").write_text(json.dumps({"machine": f"{n}-core", "rows": [row]}), "utf-8")
    assert tb.main(["--results", str(tmp_path / "4.json"), str(tmp_path / "8.json")]) == 0
    out = capsys.readouterr().out
    assert "### 4-core" in out and "### 8-core" in out and "| Qwen3-1.7B | 1500 | 12.3 |" in out
    assert "Chosen (8-core): **Qwen3-1.7B**" in out
