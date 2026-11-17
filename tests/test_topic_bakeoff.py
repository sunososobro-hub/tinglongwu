"""#610 / #1378: scripts/topic_bakeoff.py scoring, model pick, table, and the harness with a fake summarizer."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import topic_bakeoff as tb  # noqa: E402  in sys.modules, so spawn (Windows/macOS) can pickle Busy's target


def test_help_runs():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "topic_bakeoff.py"), "--help"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0 and "--model" in r.stdout and "--fetch" in r.stdout


def test_segment_counts_meet_the_issue():
    n = {lang: sum(s["lang"] == lang for s in tb.SEGMENTS) for lang in ("zh", "en", "ru")}
    assert n["zh"] >= 10 and n["en"] >= 10 and n["ru"] >= 5
    words = sum(len(t.split()) for _, t in tb._FIVE_MIN)
    assert 650 <= words <= 900  # about 5 minutes of speech at ~150 words per minute


def test_rubric_score_variants():
    assert tb.rubric_score("Budget: Two Backend engineers by march, Q2", ["two|2", "March", "Q2", "Monday"]) == 0.75
    assert tb.rubric_score("預算 300萬", ["三百萬|300萬"]) == 1.0


def test_simplified_chinese_notes_count_the_same_facts():
    pytest.importorskip("opencc")
    seg = {"id": "zh", "lang": "zh", "lines": [("me", "行銷預算三百萬，週五寄出")], "rubric": ["三百萬", "週五"]}
    assert tb.score_segment(seg, "TITLE: 预算\n- 行销预算三百万\n- 周五寄出")["facts"] == 1.0
    assert tb.rubric_score("", ["a"]) == 0.0


def test_invented_numbers():
    tr = "行銷預算是三百萬，研發預算是八百萬，十月底前提交。"
    assert tb.invented_numbers("行銷 300 萬、研發 800 萬，10 月底", tr) == []
    assert tb.invented_numbers("行銷 350 萬", tr) == ["350"]
    assert tb.invented_numbers("The outage lasted 47 minutes; alerts 30 days ahead", "lasted 47 minutes, 30 days") == []
    assert tb.invented_numbers("The outage lasted 45 minutes", "lasted 47 minutes") == ["45"]
    assert tb.invented_numbers("1. first point", "nothing") == []  # list numbering is not a fact


def test_language_ok():
    assert tb.language_ok("預算確認\n行銷預算三百萬\nme: 週五前寄行銷計畫", "zh")
    assert not tb.language_ok("Budget check\nMarketing budget is 3 million", "zh")
    assert tb.language_ok("Бюджет на рекламу\nВыставка в Казани\nme: смета до пятницы", "ru")
    assert not tb.language_ok("Advertising budget\nExhibition in Kazan", "ru")
    assert tb.language_ok("Hire a React engineer\nPost the job on Monday", "en")
    assert not tb.language_ok("", "en")


def _row(name, ram, s5, delta=0.0, facts=0.8, invented=0, zh_lang_ok=10):
    q = {"zh": {"n": 10, "facts": facts, "invented": invented, "lang_ok": zh_lang_ok},
         "en": {"n": 10, "facts": facts, "invented": 0, "lang_ok": 10},
         "ru": {"n": 5, "facts": facts, "invented": 0, "lang_ok": 5}}
    return {"model": name, "peak_ram_mb": ram, "sec_5min_topic": s5, "sec_per_topic": 5.0, "file_mb": ram / 2,
            "subtitle_p50_delta_pct": delta, "quality": q}


def test_pick_best_quality_within_budget():
    rows = [_row("big", 3500, 40, facts=0.95),           # over 3 GB
            _row("slow", 2000, 75, facts=0.9),           # over 60 s
            _row("good", 1800, 50, facts=0.85),
            _row("small", 900, 20, facts=0.7),
            _row("liar", 1000, 20, facts=0.9, invented=8),  # invented numbers cost quality
            _row("english", 800, 10, facts=0.95, zh_lang_ok=5),  # answers Chinese meetings in English
            _row("vague", 800, 10, facts=0.5)]  # misses most key facts
    assert tb.pick_model(rows)["model"] == "good"
    assert tb.budget(rows[5]) == "over: quality" and tb.budget(rows[6]) == "over: quality"
    assert tb.budget(rows[0]) == "over: RAM" and tb.budget(rows[1]) == "over: time"
    assert tb.pick_model(rows[:2]) is None


def test_table_names_fallback_when_nothing_fits(tmp_path, capsys):
    rows = [_row("slow", 2000, 75, facts=0.9), _row("big", 3500, 40, facts=0.95)]
    (tmp_path / "a.json").write_text(json.dumps({"machine": "4 vCPU", "rows": rows[:1]}), "utf-8")
    (tmp_path / "b.json").write_text(json.dumps({"machine": "4 vCPU", "rows": rows[1:]}), "utf-8")
    assert tb.main(["--results", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 0
    out = capsys.readouterr().out
    assert "| slow | 1000 | 2000 | 75.0 |" in out and "| big |" in out  # per-model jobs merge into one table
    assert "none meets the budget; best quality fallback: **big**" in out


def test_bench_model_with_fake_summarizer():
    pytest.importorskip("tinglongwu.topics")
    prompts = []

    def summarize(prompt):  # echoes the transcript as notes, so every fact is present and nothing is invented
        prompts.append(prompt)
        body = prompt.split("Transcript:\n", 1)[1]
        return "TITLE: notes\n" + "\n".join("- " + ln.split("] ", 1)[1] for ln in body.splitlines())

    def probe(stop, **kw):
        return 0.010 if stop is None else 0.0105

    row = tb.bench_model("fake", summarize, 100.0, probe=probe, busy=1, peak=lambda: 350.0)
    assert len(prompts) == len(tb.SEGMENTS) + 1 and "[me] is the user" in prompts[0]  # the app's prompt
    q = row["quality"]
    assert q["zh"]["n"] == 10 and q["en"]["n"] == 10 and q["ru"]["n"] == 5
    assert all(v["invented"] == 0 and v["lang_ok"] == v["n"] for v in q.values())
    # parse_summary keeps 5 key points, so the echo misses facts past the 5th line only
    assert all(v["facts"] >= 0.75 for v in q.values()), q
    assert row["peak_ram_mb"] == 250.0 and abs(row["subtitle_p50_delta_pct"] - 5.0) < 1e-6
    assert row["sec_5min_topic"] >= 0.0 and row["busy_cores"] == 1


def test_bench_model_raises_when_the_5min_topic_fails():
    pytest.importorskip("tinglongwu.topics")
    seg = tb.SEGMENTS[:1]

    def summarize(prompt):  # short segments work, the long 5-minute topic crashes (e.g. context overflow)
        if "Carlos" in prompt:
            raise MemoryError("ctx")
        return "TITLE: t\n- ok"

    with pytest.raises(RuntimeError, match="5-minute topic failed"):
        tb.bench_model("bad", summarize, 0.0, segments=seg, probe=lambda stop, **kw: 0.01, peak=lambda: 1.0)


def test_peak_rss_linux_kib_is_decimal_mb(monkeypatch):
    resource = pytest.importorskip("resource")
    monkeypatch.setattr(tb.sys, "platform", "linux")
    monkeypatch.setattr(resource, "getrusage", lambda who: type("U", (), {"ru_maxrss": 1_000_000})())
    assert tb.peak_rss_mb() == pytest.approx(1024.0)  # 1,000,000 KiB = 1024 MB, not 1000


def test_fetch_picks_the_quant_and_records_the_revision(tmp_path):
    class Sib:
        def __init__(self, f):
            self.rfilename = f

    class Hub:
        def model_info(self, repo):
            return type("I", (), {"sha": "a" * 40, "siblings": [Sib("m-Q8_0.gguf"), Sib("m-Q4_K_M.gguf")]})()

        def hf_hub_download(self, repo, f, revision, local_dir):
            assert revision == "a" * 40
            return str(Path(local_dir) / f)

    got = tb.fetch(["qwen2.5-1.5b"], tmp_path, hub=Hub())
    assert got["qwen2.5-1.5b"].endswith("m-Q4_K_M.gguf")
    src = json.loads((tmp_path / "qwen2.5-1.5b" / "source.json").read_text("utf-8"))
    assert src == {"repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF", "revision": "a" * 40, "file": "m-Q4_K_M.gguf"}
    with pytest.raises(ValueError):
        tb.parse_names("nope")
