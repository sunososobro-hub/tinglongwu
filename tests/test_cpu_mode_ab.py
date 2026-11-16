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


class _Done:
    def __init__(self, rc):
        self.returncode = rc


def _fake_run(rc, write=True):
    def run(cmd):
        out = Path(cmd[cmd.index("--out") + 1])
        if write:
            out.write_text(json.dumps(rep(40.0, 0.05, [1.0])))
        return _Done(rc)
    return run


def test_run_eval_keeps_a_report_that_missed_the_smoke_budget(tmp_path):
    # exit 1 = the smoke wrote its report but missed PASS / its time budget on a slow CPU: the A/B still needs it
    ab.run_eval("", "d", tmp_path / "base.json", tmp_path / "base.jsonl", run=_fake_run(1))
    assert (tmp_path / "base.json").exists()


def test_run_eval_errors_without_a_fresh_report(tmp_path):
    import subprocess

    stale = tmp_path / "base.json"
    stale.write_text("{}")  # left from an older run: must not be reused
    for run in (_fake_run(1, write=False), _fake_run(2)):
        try:
            ab.run_eval("", "d", stale, tmp_path / "base.jsonl", run=run)
        except subprocess.CalledProcessError:
            pass
        else:
            raise AssertionError("expected CalledProcessError")
    assert not stale.exists() or json.loads(stale.read_text()) != {}


def test_eval_cpu_mode_asr_settings():
    from types import SimpleNamespace

    from tlwu_eval.__main__ import cpu_mode_asr

    off = SimpleNamespace(cpu_mode=False, model=None)
    assert cpu_mode_asr(off, "cpu", "small", cores=4, ram_gb=16) == ("small", None, None)
    on = SimpleNamespace(cpu_mode=True, model=None)
    assert cpu_mode_asr(on, "cuda", "small", cores=4, ram_gb=16) == ("small", None, None)
    # 4 cores / 16 GB (a GitHub ubuntu runner): small, beam 1, 3 threads (#780 policy)
    assert cpu_mode_asr(on, "cpu", "small", cores=4, ram_gb=16) == ("small", 1, 2)
    # 2 cores: policy size base; an auto-picked small is lowered, an explicit --model small is kept
    assert cpu_mode_asr(on, "cpu", "small", cores=2, ram_gb=16) == ("base", 1, 1)
    assert cpu_mode_asr(SimpleNamespace(cpu_mode=True, model="small"), "cpu", "small", cores=2, ram_gb=16)[0] == "small"


def _mt_rep(pair_rows):
    """{pair: [(id, ref_mt, mt), ...]} -> a report with per-clip MT text."""
    return {"languages": [{"pair": p, "chrf": None, "error_rate": 0.0,
                           "clips": [{"id": i, "ref_mt": r, "mt": h, "latency_s": 1.0} for i, r, h in rows]}
                          for p, rows in pair_rows.items()]}


REFS = [f"line {k} says something quite different {k * 7}" for k in range(20)]


def test_chrf_drop_ci_identical_outputs_is_zero():
    rows = [(f"c{k}", r, r) for k, r in enumerate(REFS)]
    ci = ab.chrf_drop_ci(_mt_rep({"en->zh": rows}), _mt_rep({"en->zh": rows}), n=200)
    assert ci["drop"] == 0.0 and ci["lo"] == 0.0 and ci["hi"] == 0.0 and ci["n_clips"] == 20
    assert "inside the target" in ab.ci_line(ci)


def test_chrf_drop_ci_brackets_the_drop_and_flags_noise():
    a = [(f"c{k}", r, r) for k, r in enumerate(REFS)]
    # B garbles 2 of 20 clips: a real drop, but carried by 2 clips -> wide interval
    b = [(f"c{k}", r, "zzz qqq" if k in (3, 11) else r) for k, r in enumerate(REFS)]
    ci = ab.chrf_drop_ci(_mt_rep({"en->zh": a}), _mt_rep({"en->zh": b}), n=500, seed=1)
    assert ci["drop"] > 1.0
    assert ci["lo"] <= ci["drop"] <= ci["hi"]
    assert ci["lo"] < 1.0 < ci["hi"]  # 2 changed clips out of 20: not separable from the 1.0 limit
    assert "not separable" in ab.ci_line(ci)
    # same draw -> same interval (seeded)
    assert ab.chrf_drop_ci(_mt_rep({"en->zh": a}), _mt_rep({"en->zh": b}), n=500, seed=1) == ci


def test_chrf_drop_ci_over_target_when_every_clip_drops():
    a = [(f"c{k}", r, r) for k, r in enumerate(REFS)]
    b = [(f"c{k}", r, r[: len(r) // 2]) for k, r in enumerate(REFS)]
    ci = ab.chrf_drop_ci(_mt_rep({"en->zh": a, "zh->en": a}), _mt_rep({"en->zh": b, "zh->en": b}), n=300)
    assert ci["lo"] > 1.0 and "over the target" in ab.ci_line(ci) and ci["n_clips"] == 40


def test_chrf_drop_ci_none_without_clip_text():
    assert ab.chrf_drop_ci(rep(40.0, 0.05, [1.0]), rep(39.0, 0.05, [1.0])) is None
    assert "n/a" in ab.ci_line(None)


def test_main_prints_the_interval(tmp_path, capsys):
    a = [(f"c{k}", r, r) for k, r in enumerate(REFS)]
    ra, rb = tmp_path / "base.json", tmp_path / "cpu.json"
    ra.write_text(json.dumps(_mt_rep({"en->zh": a})))
    rb.write_text(json.dumps(_mt_rep({"en->zh": a})))
    assert ab.main([str(ra), str(rb)]) == 0
    assert "paired bootstrap 95% interval [0.00, 0.00] over 20 clips" in capsys.readouterr().out


def test_chrf_drop_ci_missing_mt_is_no_data_not_an_empty_translation():
    a = [(f"c{k}", r, r) for k, r in enumerate(REFS)]
    rb = _mt_rep({"en->zh": a})
    for c in rb["languages"][0]["clips"][:5]:
        del c["mt"]  # B's run lost these 5 clips' MT output: no measurement, not a garbled line
    ci = ab.chrf_drop_ci(_mt_rep({"en->zh": a}), rb, n=200)
    assert ci["drop"] == 0.0 and ci["hi"] == 0.0 and ci["n_clips"] == 15
    # a present empty translation is a real result and still counts as a drop
    rb2 = _mt_rep({"en->zh": [(i, r, "" if k < 5 else h) for k, (i, r, h) in enumerate(a)]})
    assert ab.chrf_drop_ci(_mt_rep({"en->zh": a}), rb2, n=200)["drop"] > 1.0


def test_chrf_drop_ci_none_when_a_scored_pair_has_no_clip_refs():
    a = [(f"c{k}", r, r) for k, r in enumerate(REFS)]
    ra, rb = _mt_rep({"en->zh": a}), _mt_rep({"en->zh": a})
    # zh->en is in the table (aggregate chrF 50 vs 40) but has no per-clip text: no interval for a subset
    for rep_, v in ((ra, 50.0), (rb, 40.0)):
        rep_["languages"].append({"pair": "zh->en", "chrf": v, "error_rate": 0.0, "clips": [{"latency_s": 1.0}]})
    assert ab.chrf_drop_ci(ra, rb, n=100) is None
