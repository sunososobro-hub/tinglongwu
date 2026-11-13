"""#123: --history appends one line per smoke run; --trend draws one <svg> per metric."""
import json

from tlwu_eval import history
from tlwu_eval.__main__ import main

from test_eval_smoke import Clock, fake_engines, make_data


def test_two_smoke_runs_two_lines_and_trend_html(tmp_path, capsys):
    data = make_data(tmp_path, langs=("en", "ru"))
    hist, rep = tmp_path / "h.jsonl", tmp_path / "r.json"
    for _ in range(2):
        clock = Clock()
        assert main(["--smoke", "--data", str(data), "--out", str(rep), "--no-noise", "--history", str(hist)],
                    engines=fake_engines(clock), totals=(16000, None), clock=clock) == 0
    lines = hist.read_text("utf-8").splitlines()
    assert len(lines) == 2
    e = json.loads(lines[0])
    assert {"date", "commit", "chrf", "mt_median_s", "asr_rtf", "peak_vram_mib", "hallucination"} <= set(e)
    assert set(e["chrf"]) == {"en->zh", "ru->zh"}
    chrf = json.loads(rep.read_text("utf-8"))["languages"][0]["chrf"]
    assert e["chrf"]["en->zh"] == round(chrf, 2)
    out = tmp_path / "trend.html"
    assert main(["--trend", str(hist), "--out", str(out)]) == 0
    page = out.read_text("utf-8")
    assert page.count("<svg") == len(history.METRICS)
    assert f"{chrf:.2f}" in page and "<polyline" in page


def test_trend_missing_file_is_setup_error(tmp_path, capsys):
    assert main(["--trend", str(tmp_path / "none.jsonl"), "--out", str(tmp_path / "t.html")]) == 2
