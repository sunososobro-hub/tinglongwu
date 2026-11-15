"""#642: per-language eval matrix from eval/languages.json (tlwu_eval.langmatrix + `tlwu_eval --pairs`)."""
import json
from pathlib import Path

import pytest

from tlwu_eval import gate, langmatrix
from tlwu_eval.__main__ import main

from test_eval_smoke import Clock, fake_engines, make_data

ROOT = Path(__file__).resolve().parents[1]
WANT = {"en", "ja", "ko", "ru", "hi", "vi", "th", "id", "es", "de", "fr"}


def test_committed_config_lists_every_language_and_matches_the_baseline():
    entries = langmatrix.load_config()
    pairs = [e["pair"] for e in entries]
    assert {p.split("->")[0] for p in pairs if p.endswith("->zh")} == WANT and "zh->en" in pairs
    base = gate.validate_baseline(json.loads((ROOT / "eval" / "baseline.json").read_text("utf-8")))
    for p in pairs:  # every listed pair has a valid one-pair gate baseline
        assert list(langmatrix.pair_baseline(p, base)["pairs"]) == [p]
    jobs, unpinned = langmatrix.matrix(entries)
    assert {j["pair"] for j in jobs} | set(unpinned) == set(pairs)
    for j in jobs:  # a job never asks for more clips than its pin file has
        pins = json.loads((ROOT / j["pins"]).read_text("utf-8"))
        assert 1 <= j["clips"] <= langmatrix.pinned_clips(pins, j["src"])


@pytest.mark.parametrize("bad, msg", [
    ({"pairs": []}, "non-empty"),
    ({"pairs": [{"pair": "en-zh", "pins": "eval/x.json", "clips": 5}]}, "look like"),
    ({"pairs": [{"pair": "en->en", "pins": "eval/x.json", "clips": 5}]}, "same"),
    ({"pairs": [{"pair": "en->zh", "pins": "../x.json", "clips": 5}]}, "pin file"),
    ({"pairs": [{"pair": "en->zh", "pins": "eval/x.json", "clips": 0}]}, "1..50"),
    ({"pairs": [{"pair": "en->zh", "pins": "eval/x.json", "clips": 5}] * 2}, "twice"),
])
def test_invalid_config_is_a_setup_error(tmp_path, bad, msg):
    f = tmp_path / "languages.json"
    f.write_text(json.dumps(bad), "utf-8")
    with pytest.raises(langmatrix.ConfigError, match=msg):
        langmatrix.load_config(f)
    assert langmatrix.main(["--config", str(f), "matrix"]) == 2


def test_matrix_skips_unpinned_pairs_and_caps_clips(tmp_path):
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "a_pins.json").write_text(json.dumps({"clips": [{"lang": "en"}] * 3 + [{"lang": "zh"}]}), "utf-8")
    entries = [{"pair": "en->zh", "src": "en", "tgt": "zh", "pins": "eval/a_pins.json", "clips": 20},
               {"pair": "zh->en", "src": "zh", "tgt": "en", "pins": "eval/a_pins.json", "clips": 20},
               {"pair": "hi->zh", "src": "hi", "tgt": "zh", "pins": "eval/missing.json", "clips": 20},
               {"pair": "fr->zh", "src": "fr", "tgt": "zh", "pins": "eval/a_pins.json", "clips": 20}]
    jobs, unpinned = langmatrix.matrix(entries, tmp_path)
    assert [(j["pair"], j["clips"], j["data"]) for j in jobs] == [("en->zh", 3, "eval_data/a_pins"), ("zh->en", 1, "eval_data/a_pins")]
    assert unpinned == ["hi->zh", "fr->zh"]


def _run(tmp_path, pair, **fake):
    data = tmp_path / "d"
    if not data.exists():
        data.mkdir()
        make_data(data, langs=("en", "zh"), n=3)
    out = tmp_path / f"{pair.replace('>', '')}.json"
    clock = Clock()
    main(["--smoke", "--data", str(data), "--pairs", pair, "--clips", "3", "--no-noise", "--out", str(out)],
         engines=fake_engines(clock, **fake), totals=(16000, None), clock=clock)
    return json.loads(out.read_text("utf-8"))


def test_pairs_flag_runs_only_that_pair(tmp_path):
    rep = _run(tmp_path, "zh->en")
    assert [lg["pair"] for lg in rep["languages"]] == ["zh->en"] and len(rep["languages"][0]["clips"]) == 3
    assert rep["languages"][0]["chrf"] is not None
    with pytest.raises(SystemExit):
        main(["--smoke", "--pairs", "en-zh"])


def test_right_target_passes_and_wrong_target_turns_that_pair_red(tmp_path):
    base = {"model": "m", "engine": "e", "clips": 3, "pairs": {"en->zh": {"chrf": 0, "chrf_margin": 0, "mt_median_s_max": 1}},
            "matrix": {"pairs": {"en->zh": {"chrf": 10.0, "chrf_margin": 2.0, "mt_median_s_max": 1.0},
                                 "zh->en": {"chrf": None, "chrf_margin": None, "mt_median_s_max": 1.0}}}}
    good = _run(tmp_path, "en->zh")
    ok, line = langmatrix.summary_line(good, "en->zh")
    assert ok and "3 clips, ASR error rate 0.00%, chrF 100.00" in line
    rows = gate.compare(good, langmatrix.pair_baseline("en->zh", base))
    assert [r["result"] for r in rows] == ["PASS"]
    # en pointed at ja: the clips have no ref_ja -> no chrF -> red, and the pair has no baseline entry -> red
    wrong = _run(tmp_path, "en->ja")
    ok, line = langmatrix.summary_line(wrong, "en->ja")
    assert not ok and "no ref_ja" in line
    with pytest.raises(gate.GateError, match="no matrix entry for en->ja"):
        langmatrix.pair_baseline("en->ja", base)
    # a chrF drop beyond the margin on a recorded pair is red
    bad = _run(tmp_path, "en->zh", mt_text="完全不同的句子")
    assert [r["result"] for r in gate.compare(bad, langmatrix.pair_baseline("en->zh", base))] == ["FAIL"]


def test_cli_baseline_and_summary(tmp_path, capsys):
    rep = _run(tmp_path, "en->zh")
    r = tmp_path / "r.json"
    r.write_text(json.dumps(rep), "utf-8")
    out = tmp_path / "b.json"
    assert langmatrix.main(["baseline", "en->zh", "--out", str(out)]) == 0
    assert list(json.loads(out.read_text("utf-8"))["pairs"]) == ["en->zh"]
    assert langmatrix.main(["baseline", "en->xx", "--out", str(out)]) == 2
    assert langmatrix.main(["summary", str(r), "en->zh"]) == 0
    assert langmatrix.main(["summary", str(r), "zh->en"]) == 1  # pair not in the report
    assert "FAIL (no clips evaluated)" in capsys.readouterr().out


def test_workflow_builds_its_matrix_from_the_config():
    import yaml
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "eval-langs.yml").read_text("utf-8"))
    jobs = wf["jobs"]
    assert "langmatrix matrix" in jobs["matrix"]["steps"][-1]["run"]
    strat = jobs["pair"]["strategy"]
    assert strat["fail-fast"] is False and "fromJSON(needs.matrix.outputs.jobs)" in strat["matrix"]["include"]
    assert jobs["pair"]["timeout-minutes"] <= 30
