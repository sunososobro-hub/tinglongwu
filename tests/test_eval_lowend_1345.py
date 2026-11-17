"""#1345: the low-end report shows the signed gap to the 2.5 s per-line target; pass/fail is unchanged."""
from __future__ import annotations

import json

from tlwu_eval import lowend
from tlwu_eval.__main__ import main

from test_eval_lowend_750 import _baseline, _smoke, data_dir  # noqa: F401


def test_gap_in_table_and_result_json(data_dir, capsys):  # noqa: F811
    base = _baseline(data_dir, 0.6, 0.6)
    rep = _smoke(data_dir, "r", mt_s=0.2)  # 0.6 s per line
    capsys.readouterr()
    assert main(["--lowend-gate", str(rep), "--lowend-baseline", str(base)]) == 0
    out = capsys.readouterr().out
    assert "Target 2.5 s per line (not a gate): p50 0.6 s (-1.90 s), p90 0.6 s (-1.90 s)" in out
    assert json.loads(rep.read_text("utf-8"))["lowend_target"] == {
        "target_s": 2.5, "latency_p50_gap_s": -1.9, "latency_p90_gap_s": -1.9}


def test_over_target_is_positive_and_does_not_fail(data_dir):  # noqa: F811
    base = _baseline(data_dir, 2.9, 2.9)
    rep = _smoke(data_dir, "slow", mt_s=2.5)  # 2.9 s per line, same as the baseline
    code, md = lowend.run(rep, base)
    assert code == 0 and "p50 2.9 s (+0.40 s)" in md
    assert lowend.target_gap({"languages": []})["latency_p50_gap_s"] is None
