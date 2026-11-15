"""#781: scripts/bench/cpu_mode_ab.py turns two tlwu_eval reports (or a fake jsonl pair) into the A/B table."""
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("cpu_mode_ab", Path(__file__).resolve().parents[1] / "scripts/bench/cpu_mode_ab.py")
ab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ab)


def rep(chrf, err, lats):
    return {"languages": [{"pair": "en->zh", "chrf": chrf, "error_rate": err,
                           "clips": [{"latency_s": x} for x in lats]}]}


def test_summary_and_verdict_from_reports():
    a = ab.summarize(rep(40.0, 0.05, [2.0, 1.0, 3.0]))
    b = ab.summarize(rep(39.5, 0.06, [1.0, 0.5, 1.5]))
    assert a["_all"]["p50_s"] == 2.0 and b["_all"]["p50_s"] == 1.0
    v = ab.verdict(a, b)
    assert round(v["p50_drop_pct"], 1) == 50.0 and v["p50_ok"]
    assert round(v["chrf_drop"], 2) == 0.5 and v["chrf_ok"]
    assert round(v["wer_rise_pts"], 2) == 1.0 and v["wer_ok"]
    t = ab.table(a, b)
    assert "| en->zh | 2.000 | 1.000 | 40.00 | 39.50 | 5.00 | 6.00 |" in t
    assert "p50 drop 50.0%" in t


def test_missed_targets_are_reported():
    a = ab.summarize(rep(40.0, 0.05, [2.0]))
    b = ab.summarize(rep(35.0, 0.10, [1.9]))
    v = ab.verdict(a, b)
    assert not v["p50_ok"] and not v["chrf_ok"] and not v["wer_ok"]
    assert "missed" in ab.table(a, b)


def test_fake_jsonl_pair_overrides_report_latency(tmp_path, capsys):
    ra, rb = tmp_path / "base.json", tmp_path / "cpu.json"
    ra.write_text(json.dumps(rep(40.0, 0.05, [9.0])))
    rb.write_text(json.dumps(rep(40.0, 0.05, [9.0])))
    (tmp_path / "base.jsonl").write_text('{"pair":"en->zh","id":"a","total":4.0}\n{"pair":"en->zh","id":"b","total":2.0}\n')
    (tmp_path / "cpu.jsonl").write_text('{"pair":"en->zh","id":"a","total":2.0}\n{"pair":"en->zh","id":"b","total":1.0}\n')
    assert ab.main([str(ra), str(rb)]) == 0
    out = capsys.readouterr().out
    assert "| **all** | 3.000 | 1.500 |" in out and "p50 drop 50.0%" in out


def test_bad_report_exits_2(tmp_path, capsys):
    bad = tmp_path / "x.json"
    bad.write_text("{not json")
    assert ab.main([str(bad), str(bad)]) == 2


def test_wer_limit_is_inclusive_despite_float_noise():
    v = ab.verdict(ab.summarize(rep(40.0, 0.05, [1.0])), ab.summarize(rep(40.0, 0.07, [1.0])))
    assert v["wer_ok"]
    v = ab.verdict(ab.summarize(rep(40.0, 0.05, [1.0])), ab.summarize(rep(40.0, 0.0701, [1.0])))
    assert not v["wer_ok"]


def test_different_pair_sets_exit_2(tmp_path, capsys):
    ra, rb = tmp_path / "a.json", tmp_path / "b.json"
    ra.write_text(json.dumps({"languages": rep(40.0, 0.05, [10.0])["languages"]
                              + [{"pair": "fr->zh", "chrf": 30.0, "error_rate": 0.1, "clips": [{"latency_s": 1.0}]}]}))
    rb.write_text(json.dumps({"languages": [{"pair": "fr->zh", "chrf": 30.0, "error_rate": 0.1,
                                             "clips": [{"latency_s": 1.0}]}]}))
    assert ab.main([str(ra), str(rb)]) == 2
    assert "en->zh" in capsys.readouterr().err


def test_run_without_cpu_args_is_rejected(tmp_path):
    import pytest
    with pytest.raises(SystemExit) as e:
        ab.main(["--run", "--out-dir", str(tmp_path / "o")])
    assert e.value.code == 2
    assert not (tmp_path / "o").exists()


def test_p50_limit_is_inclusive_despite_float_noise():
    v = ab.verdict(ab.summarize(rep(40.0, 0.05, [3.0])), ab.summarize(rep(40.0, 0.05, [2.1])))
    assert v["p50_ok"]
    v = ab.verdict(ab.summarize(rep(40.0, 0.05, [3.0])), ab.summarize(rep(40.0, 0.05, [2.11])))
    assert not v["p50_ok"]


def test_stale_sidecar_with_extra_pair_exits_2(tmp_path, capsys):
    ra, rb = tmp_path / "base.json", tmp_path / "cpu.json"
    ra.write_text(json.dumps(rep(40.0, 0.05, [10.0])))
    rb.write_text(json.dumps(rep(40.0, 0.05, [10.0])))
    (tmp_path / "base.jsonl").write_text('{"pair":"en->zh","id":"a","total":10.0}\n{"pair":"fr->zh","id":"b","total":100.0}\n')
    assert ab.main([str(ra), str(rb)]) == 2
    assert "fr->zh" in capsys.readouterr().err
