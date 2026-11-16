"""#1158: summary and choice logic of scripts/bench/extension_spike.py (the browser run itself is CI-only)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def spike():
    spec = importlib.util.spec_from_file_location("bench_extension_spike_1158",
                                                  ROOT / "scripts" / "bench" / "extension_spike.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _clip(cid, lang, asr, asr_text, asr_s, mt):
    c = {"id": cid, "lang": lang, "asr": asr, "ref_text": "the cat sat" if lang == "en" else "你好世界",
         "asr_text": asr_text, "asr_s": asr_s, "mt": mt}
    c["ref_en" if lang == "zh" else "ref_zh"] = "貓坐著" if lang == "en" else "hello world"
    return c


def _report():
    clips = []
    for asr, speed, good in (("whisper-tiny", 1.0, False), ("whisper-small", 9.0, True)):
        clips += [
            _clip(f"en{i}", "en", asr, "the cat sat" if good else "a cat", speed,
                  {"opus-mt": {"text": "貓坐著" if good else "狗", "s": 0.5},
                   "nllb-600m": {"text": "貓坐著", "s": 2.0}})
            for i in range(3)
        ] + [
            _clip(f"zh{i}", "zh", asr, "你好世界", speed,
                  {"opus-mt": {"text": "hello world", "s": 0.5}, "nllb-600m": {"text": "hello world", "s": 2.0}})
            for i in range(3)
        ]
    return {
        "env": {"browser": "x", "threads": 4, "isolated": True, "webgpu": False, "clips": 6},
        "asr": {"whisper-tiny": {"bytes": 40e6, "cold_s": 3, "warm_s": 1},
                "whisper-small": {"bytes": 250e6, "cold_s": 20, "warm_s": 5}},
        "mt": {"opus-mt": {"bytes": 300e6, "cold_s": 10, "warm_s": 2},
               "nllb-600m": {"bytes": 650e6, "cold_s": 30, "warm_s": 6}},
        "clips": clips,
    }


def test_summarize_latency_is_asr_plus_mt_per_clip(spike):
    rows = {(r["asr"], r["mt"]): r for r in spike.summarize(_report(), {"pairs": {"en->zh": {"chrf": 38.9}}})}
    assert set(rows) == {(a, m) for a in ("whisper-tiny", "whisper-small") for m in ("opus-mt", "nllb-600m")}
    r = rows[("whisper-tiny", "nllb-600m")]
    assert r["latency_median_s"] == 3.0 and r["latency_p90_s"] == 3.0
    assert r["download_mb"] == 690.0 and r["cold_load_s"] == 33 and r["warm_load_s"] == 7
    assert r["chrf"]["en->zh"]["desktop"] == 38.9 and r["chrf"]["zh->en"]["desktop"] is None
    assert r["chrf"]["en->zh"]["chrf"] == 100.0
    assert rows[("whisper-tiny", "opus-mt")]["chrf"]["en->zh"]["chrf"] == 0.0
    assert rows[("whisper-small", "opus-mt")]["asr_error"] == {"en": 0.0, "zh": 0.0}
    assert rows[("whisper-tiny", "opus-mt")]["asr_error"]["en"] == pytest.approx(0.667, abs=1e-3)


def test_pick_best_quality_within_budget_not_fastest(spike):
    rows = spike.summarize(_report())
    best = spike.pick(rows, budget_s=8.0)
    # whisper-small is better but 9.5 s+ median: over budget. Among the in-budget tiny combos nllb wins on chrF.
    assert (best["asr"], best["mt"]) == ("whisper-tiny", "nllb-600m")
    assert (spike.pick(rows, budget_s=20.0)["asr"]) == "whisper-small"
    assert spike.pick(rows, budget_s=1.0) is None


def test_pick_tie_prefers_smaller_download(spike):
    rows = [{"latency_median_s": 2, "mean_chrf": 30, "download_mb": 500, "asr": "a"},
            {"latency_median_s": 3, "mean_chrf": 30, "download_mb": 100, "asr": "b"}]
    assert spike.pick(rows)["asr"] == "b"


def test_markdown_names_choice_or_says_none(spike):
    rows = spike.summarize(_report(), {"pairs": {"en->zh": {"chrf": 38.9}}})
    md = spike.to_markdown(rows, {"runner": "gh"}, budget_s=8.0)
    assert "| whisper-tiny | nllb-600m | 690.0 |" in md
    assert "100.0 (38.9)" in md
    assert "Choice: ASR **whisper-tiny**" in md
    assert "no candidate meets the median latency budget of 1 s" in spike.to_markdown(rows, {}, budget_s=1.0)
    assert "no ASR + MT combination loaded" in spike.to_markdown([], {})


def test_failed_candidate_is_left_out_not_fatal(spike):
    rep = _report()
    rep["mt"]["nllb-600m"]["error"] = "Failed to fetch"
    rep["asr"]["whisper-small"] = {"model": "x", "error": "boom"}
    rows = spike.summarize(rep)
    assert [(r["asr"], r["mt"]) for r in rows] == [("whisper-tiny", "opus-mt")]


def test_merge_joins_matrix_jobs(spike):
    a, b = _report(), _report()
    a["asr"].pop("whisper-small")
    a["clips"] = [c for c in a["clips"] if c["asr"] == "whisper-tiny"]
    b["asr"].pop("whisper-tiny")
    b["clips"] = [c for c in b["clips"] if c["asr"] == "whisper-small"]
    m = spike.merge([a, b])
    assert set(m["asr"]) == {"whisper-tiny", "whisper-small"} and len(m["clips"]) == 12
    assert m["env"]["clips"] == 6


def test_main_merge_writes_md_and_json(spike, tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(_report()), encoding="utf-8")
    assert spike.main(["--merge", str(p), "--md", str(tmp_path / "s.md"), "--json", str(tmp_path / "s.json"),
                       "--runner", "test"]) == 0
    s = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert s["choice"] == {"asr": "whisper-tiny", "mt": "nllb-600m"} and s["budget_s"] == 8.0
    assert (tmp_path / "s.md").read_text(encoding="utf-8").startswith("Runner: test,")


def test_every_mt_candidate_covers_the_smoke_pairs(spike):
    pairs = {"en->zh", "ja->zh", "ru->zh", "zh->en"}
    for name, chains in spike.MT.items():
        assert set(chains) == pairs, name


def test_merge_keeps_each_jobs_own_mt_result(spike):
    # job A: nllb failed to load; job B: nllb loaded. B's combinations must survive with B's own load times.
    a, b = _report(), _report()
    a["asr"].pop("whisper-small")
    a["clips"] = [c for c in a["clips"] if c["asr"] == "whisper-tiny"]
    a["mt"]["nllb-600m"] = {"error": "Failed to fetch"}
    b["asr"].pop("whisper-tiny")
    b["clips"] = [c for c in b["clips"] if c["asr"] == "whisper-small"]
    b["mt"]["opus-mt"] = {"bytes": 300e6, "cold_s": 99, "warm_s": 2}
    rows = {(r["asr"], r["mt"]): r for r in spike.summarize(spike.merge([a, b]))}
    assert ("whisper-small", "nllb-600m") in rows and ("whisper-tiny", "nllb-600m") not in rows
    assert rows[("whisper-small", "opus-mt")]["cold_load_s"] == 119
    assert rows[("whisper-tiny", "opus-mt")]["cold_load_s"] == 13
