"""#120: per-hardware-tier eval - presets, the four metrics (COMET, chrF, hallucination, latency) and the chart."""
import json
import subprocess
import sys
from types import SimpleNamespace

import tlwu_eval.__main__ as cli
from tlwu_eval import tiers
from tlwu_eval.__main__ import main

from test_eval_smoke import Clock, fake_engines, make_data


class FakeComet:
    def __init__(self):
        self.calls = []

    def predict(self, data, batch_size, gpus, progress_bar):
        self.calls.append((data, gpus))
        return SimpleNamespace(system_score=0.8123)


def run_tier(tmp_path, name, *extra, comet=None, n=6):
    """Run `--smoke --tier name` with fake engines; returns (exit code, report, the parsed args the engines got)."""
    (tmp_path / name).mkdir()
    data = make_data(tmp_path / name, langs=("en", "ru"), n=n)
    clock, seen, out = Clock(), {}, tmp_path / f"{name}.json"
    real = cli.real_engines
    cli.real_engines = lambda a: (seen.setdefault("a", a), fake_engines(clock))[1]
    try:
        code = main(["--smoke", "--tier", name, "--data", str(data), "--out", str(out), *extra, "--no-noise"],
                    totals=(16000, None), clock=clock, comet_model=comet)
    finally:
        cli.real_engines = real
    return code, json.loads(out.read_text("utf-8")), seen["a"]


def test_tier_preset_sets_engine_and_summary(tmp_path, capsys):
    code, rep, a = run_tier(tmp_path, "cpu-low")
    assert code == 0
    assert (a.model, a.engine, a.device) == ("base", "opus", "cpu")
    t = rep["tier"]
    assert t["tier"] == "cpu-low" and t["chrf"] == 100.0 and t["comet"] is None
    assert t["hallucination"] == {"count": 0, "total": 10, "rate": 0.0}
    assert t["latency_s"]["median"] > 0 and t["baseline"]["count"] == 10
    assert "tier cpu-low: chrF 100.0 COMET None hallucination 0/10 (0.00)" in capsys.readouterr().out


def test_explicit_flags_override_the_preset(tmp_path):
    _, _, a = run_tier(tmp_path, "mid-gpu", "--model", "large-v3-turbo")
    assert (a.model, a.engine, a.device) == ("large-v3-turbo", "hymt", "cuda")


def test_per_lang_and_comet(tmp_path):
    comet = FakeComet()
    _, rep, _ = run_tier(tmp_path, "high", "--per-lang", "4", "--comet", comet=comet, n=6)
    assert [len(lg["clips"]) for lg in rep["languages"]] == [4, 4]
    assert rep["tier"]["comet"] == 81.23 and rep["tier"]["pairs"][0]["comet"] == 81.23
    data, gpus = comet.calls[0]
    assert gpus == 1 and len(data) == 4 and set(data[0]) == {"src", "mt", "ref"}


def test_hallucination_counts_stock_phrases_and_invented_text():
    assert tiers.is_hallucinated("кошка сидела на коврике", "Продолжение следует...", "ru")
    assert tiers.is_hallucinated("the cat", "one two three four five six", "en")  # 6 errors on 2 words
    assert not tiers.is_hallucinated("the cat sat on the mat", "the cat sat on a mat", "en")
    assert not tiers.is_hallucinated("the cat", "", "en")
    rep = {"languages": [{"pair": "ru->zh", "chrf": 50.0, "clips": [
        {"ref_text": "кошка сидела", "asr": "Продолжение следует", "latency_s": 1.0, "mt": "x", "ref_mt": "y"},
        {"ref_text": "кошка сидела", "asr": "кошка сидела", "latency_s": 2.0, "mt": "x", "ref_mt": "y"}]}]}
    s = tiers.summarize(rep, "mid-gpu")
    assert s["hallucination"] == {"count": 1, "total": 2, "rate": 50.0}
    assert s["pairs"][0]["hallucination"]["texts"] == ["Продолжение следует"]


def test_chart_compares_tiers_with_baseline(tmp_path):
    reports = [str(tmp_path / f"{n}.json") for n in ("cpu-low", "mid-gpu")]
    for n in ("cpu-low", "mid-gpu"):
        run_tier(tmp_path, n)
    out = tmp_path / "tiers.html"
    r = subprocess.run([sys.executable, "-m", "tlwu_eval", "--chart", str(out), *reports],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    page = out.read_text("utf-8")
    assert page.count("<svg") == 6 and "cpu-low" in page and "mid-gpu" in page
    assert "small ru (baseline) 50.00%" in page and "10/20" in page


def test_chart_rejects_report_without_tier(tmp_path):
    bad = tmp_path / "plain.json"
    bad.write_text(json.dumps({"mode": "smoke"}), "utf-8")
    r = subprocess.run([sys.executable, "-m", "tlwu_eval", "--chart", str(tmp_path / "o.html"), str(bad)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "no tier summary" in r.stderr and "Traceback" not in r.stderr
