"""#361: `python -m tlwu_eval --gate REPORT.json --baseline B.json` - exit 1 on a chrF / MT latency regression."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tlwu_eval import gate
from tlwu_eval.__main__ import main

from test_eval_smoke import Clock, fake_engines, make_data

ROOT = Path(__file__).resolve().parents[1]


def baseline(**pairs):
    rows = {p: {"chrf": 40.0, "chrf_margin": 2.0, "mt_median_s_max": 1.0} for p in ("en->zh", "ja->zh", "ru->zh", "zh->en")}
    for p, row in pairs.items():
        rows[p.replace("_", "->")] = {**rows[p.replace("_", "->")], **row}
    return {"model": "small", "engine": "hymt2-cpu", "clips": 5, "pairs": rows}


def report(**pairs):
    """pairs: en_zh=(chrf, mt_median_s); absent pairs are listed as skipped, like run_smoke does."""
    langs = [{"pair": p.replace("_", "->"), "clips": [{"id": "x"}], "chrf": c, "mt_median_s": m}
             for p, (c, m) in pairs.items()]
    return {"mode": "smoke", "languages": langs, "skipped": []}


def write(tmp_path, name, obj):
    (tmp_path / name).write_text(json.dumps(obj), "utf-8")
    return str(tmp_path / name)


def gate_cli(tmp_path, rep, base, capsys):
    code = main(["--gate", write(tmp_path, "r.json", rep), "--baseline", write(tmp_path, "b.json", base)])
    return code, capsys.readouterr()


def test_chrf_drop_beyond_margin_fails(tmp_path, capsys):
    code, out = gate_cli(tmp_path, report(en_zh=(37.5, 0.5)), baseline(), capsys)
    assert code == 1
    assert "| en->zh | 37.50 | 40.00 | -2.50 | 0.500 | 1.00 | FAIL (chrF dropped more than 2.00) |" in out.out
    assert "**eval gate: FAIL**" in out.out


def test_chrf_drop_within_margin_passes(tmp_path, capsys):
    code, out = gate_cli(tmp_path, report(en_zh=(38.123, 0.5), ru_zh=(42.0, 0.9)), baseline(), capsys)
    assert code == 0
    assert "| en->zh | 38.12 | 40.00 | -1.88 | 0.500 | 1.00 | PASS |" in out.out
    assert "| ru->zh | 42.00 | 40.00 | +2.00 | 0.900 | 1.00 | PASS |" in out.out
    assert "**eval gate: PASS**" in out.out


def test_missing_pair_is_skip_not_fail(tmp_path, capsys):
    rep = report(en_zh=(40.0, 0.5))
    rep["languages"].append({"pair": "ja->zh", "clips": [], "chrf": None, "mt_median_s": None})
    code, out = gate_cli(tmp_path, rep, baseline(), capsys)
    assert code == 0
    assert "| ja->zh | n/a | 40.00 | n/a | n/a | 1.00 | SKIP (no clips in the report) |" in out.out
    assert "| zh->en | n/a | 40.00 | n/a | n/a | 1.00 | SKIP (no clips in the report) |" in out.out


def test_margin_in_the_baseline_file_flips_the_result(tmp_path, capsys):
    rep = report(en_zh=(37.5, 0.5))
    assert gate_cli(tmp_path, rep, baseline(en_zh={"chrf_margin": 2.0}), capsys)[0] == 1
    assert gate_cli(tmp_path, rep, baseline(en_zh={"chrf_margin": 3.0}), capsys)[0] == 0
    code, out = gate_cli(tmp_path, report(en_zh=(45.0, 1.2)), baseline(), capsys)  # slow MT fails...
    assert code == 1 and "FAIL (MT median over 1s)" in out.out
    assert gate_cli(tmp_path, report(en_zh=(45.0, 1.2)), baseline(en_zh={"mt_median_s_max": 1.5}), capsys)[0] == 0


def test_null_chrf_gates_latency_only_and_missing_report_chrf_fails(tmp_path, capsys):
    base = baseline(en_zh={"chrf": None, "chrf_margin": None})
    assert gate_cli(tmp_path, report(en_zh=(1.0, 0.5)), base, capsys)[0] == 0
    assert gate_cli(tmp_path, report(en_zh=(1.0, 3.0)), base, capsys)[0] == 1
    code, out = gate_cli(tmp_path, report(en_zh=(None, 0.5)), baseline(), capsys)
    assert code == 1 and "no chrF in the report" in out.out


@pytest.mark.parametrize("mutate, msg", [
    (lambda b: b.pop("model"), "missing 'model'"),
    (lambda b: b.update(clips=0), "'clips' must be a positive integer"),
    (lambda b: b.update(pairs={}), "'pairs' must be a non-empty object"),
    (lambda b: b.update(pairs=[]), "'pairs' must be a non-empty object"),
    (lambda b: b["pairs"]["en->zh"].pop("chrf_margin"), "en->zh is missing 'chrf_margin'"),
    (lambda b: b["pairs"]["en->zh"].update(chrf=140), "chrf must be a number in 0..100"),
    (lambda b: b["pairs"]["en->zh"].update(chrf="40"), "chrf must be a number in 0..100"),
    (lambda b: b["pairs"]["en->zh"].update(chrf_margin=-1), "chrf_margin must be a number >= 0"),
    (lambda b: b["pairs"]["en->zh"].update(chrf_margin=None), "both be numbers or both be null"),
    (lambda b: b["pairs"]["en->zh"].update(mt_median_s_max=0), "mt_median_s_max must be a number > 0"),
])
def test_baseline_schema_is_validated(tmp_path, capsys, mutate, msg):
    b = baseline()
    mutate(b)
    code, out = gate_cli(tmp_path, report(en_zh=(40.0, 0.5)), b, capsys)
    assert code == 2 and msg in out.err and "Traceback" not in out.err


def test_committed_baseline_is_valid_and_regressed_fixture_exits_1():
    """The issue's verify step, through the real `python -m tlwu_eval` entry point."""
    gate.validate_baseline(json.loads((ROOT / "eval" / "baseline.json").read_text("utf-8")))
    r = subprocess.run([sys.executable, "-m", "tlwu_eval", "--gate", "tests/data/eval_gate_regressed.json",
                        "--baseline", "eval/baseline.json"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "**eval gate: FAIL**" in r.stdout and "| zh->en |" in r.stdout


def test_gate_reads_a_real_smoke_report(tmp_path, capsys):
    """--smoke writes the report, --gate reads it: the field names agree end to end."""
    data = make_data(tmp_path, langs=("en", "ru"))
    out = tmp_path / "r.json"
    clock = Clock()
    assert main(["--smoke", "--data", str(data), "--clips", "2", "--out", str(out)], engines=fake_engines(clock),
                totals=(16000, None), clock=clock) == 0
    capsys.readouterr()
    rep = json.loads(out.read_text("utf-8"))
    en = next(lg for lg in rep["languages"] if lg["pair"] == "en->zh")
    assert len(en["clips"]) == 2  # --clips N (issue spelling) = --per-lang N
    base = baseline(en_zh={"chrf": en["chrf"], "chrf_margin": 0.0})
    assert main(["--gate", str(out), "--baseline", write(tmp_path, "b.json", base)]) == 0
    assert "SKIP" in capsys.readouterr().out  # ja->zh / zh->en had no clips
    assert main(["--gate", str(out), "--baseline", write(tmp_path, "b.json", baseline(en_zh={"mt_median_s_max": 0.1}))]) == 1
