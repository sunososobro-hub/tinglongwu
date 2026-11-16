"""#643 (#619 part 4): weekly per-language verdict (tlwu_eval.weekly) + the stats-page table (stats_dashboard)."""
import base64
import json
import sys
from pathlib import Path

import yaml

from tlwu_eval import weekly

from test_eval_langmatrix_642 import _run

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import stats_dashboard as sd  # noqa: E402

BASE = {"model": "m", "engine": "e", "clips": 3, "pairs": {"en->zh": {"chrf": 0, "chrf_margin": 0, "mt_median_s_max": 1}},
        "matrix": {"pairs": {"en->zh": {"chrf": 90.0, "chrf_margin": 2.0, "mt_median_s_max": 1.0},
                             "zh->en": {"chrf": None, "chrf_margin": None, "mt_median_s_max": 1.0},
                             "ja->zh": {"chrf": 10.0, "chrf_margin": 2.0, "mt_median_s_max": 1.0}}}}


def _repo(tmp_path):
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "p_pins.json").write_text(json.dumps({"clips": [{"lang": c} for c in ("en", "zh", "ja")]}), "utf-8")
    cfg = tmp_path / "eval" / "languages.json"
    cfg.write_text(json.dumps({"pairs": [{"pair": "en->zh", "pins": "eval/p_pins.json", "clips": 3},
                                         {"pair": "zh->en", "pins": "eval/p_pins.json", "clips": 3},
                                         {"pair": "ja->zh", "pins": "eval/p_pins.json", "clips": 3},
                                         {"pair": "hi->zh", "pins": "eval/missing.json", "clips": 3}]}), "utf-8")
    bl = tmp_path / "eval" / "baseline.json"
    bl.write_text(json.dumps(BASE), "utf-8")
    return cfg, bl


def _art(arts, src, tgt, report):
    d = arts / f"eval-langs-{src}-{tgt}"
    d.mkdir(parents=True)
    (d / "r.json").write_text(json.dumps(report), "utf-8")


def test_collect_gates_each_pair_and_missing_report_is_fail(tmp_path):
    cfg, bl = _repo(tmp_path)
    arts = tmp_path / "arts"
    _art(arts, "en", "zh", _run(tmp_path, "en->zh"))                              # chrF 100 vs 90 - 2: PASS
    _art(arts, "zh", "en", _run(tmp_path, "en->zh", mt_text="完全不同的句子"))  # wrong pair in report: no clips -> FAIL
    # ja->zh: the job died before r.json -> FAIL; hi->zh unpinned -> not listed
    doc = weekly.collect(arts, cfg, bl, date="2026-11-16", run_url="https://run/1")
    v = {r["pair"]: r for r in doc["languages"]}
    assert list(v) == ["en->zh", "zh->en", "ja->zh"]
    assert v["en->zh"]["verdict"] == "PASS" and v["en->zh"]["chrf"] == 100.0 and v["en->zh"]["asr_error_rate"] == 0
    assert v["zh->en"]["verdict"] == "FAIL" and "no clips" in v["zh->en"]["why"]
    assert v["ja->zh"]["verdict"] == "FAIL" and "no report" in v["ja->zh"]["why"]


def test_chrf_drop_is_fail_and_alert_names_failing_pairs(tmp_path):
    cfg, bl = _repo(tmp_path)
    arts = tmp_path / "arts"
    _art(arts, "en", "zh", _run(tmp_path, "en->zh", mt_text="完全不同的句子"))
    doc = weekly.collect(arts, cfg, bl, date="2026-11-16", run_url="https://run/1")
    en = doc["languages"][0]
    assert en["verdict"] == "FAIL" and "chrF dropped more than 2.00" in en["why"]
    text = weekly.alert_markdown(doc)
    assert text.startswith("eval-langs weekly 2026-11-16: FAIL for en->zh, zh->en, ja->zh.")
    assert f"| en->zh | 0.00 | {en['chrf']:.2f} |" in text and "Run: https://run/1" in text


def test_cli_alert_flag_and_all_pass_is_silent(tmp_path, monkeypatch, capsys):
    good = {"date": "d", "languages": [{"pair": "en->zh", "verdict": "PASS"}]}
    p = tmp_path / "w.json"
    p.write_text(json.dumps(good), "utf-8")
    out = tmp_path / "gh"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert weekly.main(["alert", str(p), "--out", str(tmp_path / "a.md")]) == 0
    assert out.read_text().strip() == "fail=false" and not (tmp_path / "a.md").exists()
    p.write_text(json.dumps({"date": "d", "languages": [{"pair": "en->zh", "verdict": "FAIL", "chrf": 1.0}]}), "utf-8")
    assert weekly.main(["alert", str(p), "--out", str(tmp_path / "a.md")]) == 0
    assert out.read_text().split() == ["fail=false", "fail=true"] and "en->zh" in (tmp_path / "a.md").read_text()
    assert weekly.main(["alert", str(tmp_path / "nope.json"), "--out", str(tmp_path / "a.md")]) == 2


PREV = {"date": "2026-11-09", "languages": [
    {"pair": "en->zh", "asr_error_rate": 0.0812, "chrf": 38.9, "verdict": "PASS"},
    {"pair": "ja->zh", "asr_error_rate": 0.21, "chrf": 25.0, "verdict": "PASS"}]}
LATEST = {"date": "2026-11-16", "languages": [
    {"pair": "en->zh", "asr_error_rate": 0.0701, "chrf": 40.125, "verdict": "PASS"},
    {"pair": "ja->zh", "asr_error_rate": 0.2333, "chrf": 21.5, "verdict": "FAIL"},
    {"pair": "fr->zh", "asr_error_rate": 0.05, "chrf": 24.19, "verdict": "PASS"},
    "junk"]}


def test_dashboard_rows_and_deltas_from_two_results():
    assert sd.lang_verdict_rows(LATEST, PREV) == [
        {"language": "en->zh", "asr": 7.01, "mt": 40.12, "verdict": "PASS", "d_asr": -1.11, "d_mt": 1.22},
        {"language": "ja->zh", "asr": 23.33, "mt": 21.5, "verdict": "FAIL", "d_asr": 2.33, "d_mt": -3.5},
        {"language": "fr->zh", "asr": 5.0, "mt": 24.19, "verdict": "PASS", "d_asr": None, "d_mt": None}]


def test_dashboard_reads_latest_two_from_eval_results_and_renders():
    files = {"2026-11-02.json": {"languages": []}, "2026-11-09.json": PREV, "2026-11-16.json": LATEST}

    def api(path, accept=None):
        if path == "/repos/o/r/contents/langs?ref=eval-results":
            return [{"name": n} for n in files] + [{"name": "README.md"}]
        name = path.split("/langs/")[1].split("?")[0]
        return {"content": base64.b64encode(json.dumps(files[name]).encode()).decode()}

    lv = sd.collect_lang_verdicts(api, "o/r")
    assert lv["date"] == "2026-11-16" and lv["previous_date"] == "2026-11-09" and len(lv["rows"]) == 3
    page = sd.render_lang_verdicts({"lang_verdicts": lv})
    assert "<caption>2026-11-16 (vs 2026-11-09)</caption>" in page
    assert "<td>en-&gt;zh</td><td>7.01</td><td>40.12</td><td>PASS</td><td>ASR -1.11, chrF +1.22</td>" in page
    assert '<td style="color:#c0392b">FAIL</td>' in page


def test_dashboard_without_results_says_no_data():
    def boom(path, accept=None):
        raise OSError("404")
    assert sd.collect_lang_verdicts(boom, "o/r") is None
    assert 'id="langv">no data yet' in sd.render_lang_verdicts({"lang_verdicts": None})


def test_workflow_runs_weekly_and_splits_write_permissions():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "eval-langs.yml").read_text("utf-8"))
    on = wf.get("on", wf.get(True))
    assert on["schedule"] and on["workflow_dispatch"]["inputs"]["publish"]["default"] is False
    jobs = wf["jobs"]
    assert jobs["publish"]["permissions"] == {"contents": "write"}
    assert jobs["alert"]["permissions"] == {"issues": "write"}
    assert "permissions" not in jobs["weekly"] and wf["permissions"] == {"contents": "read"}
    assert "gh issue comment 61" in jobs["alert"]["steps"][-1]["run"]
    assert "langs/$D.json" in jobs["publish"]["steps"][-1]["run"]
