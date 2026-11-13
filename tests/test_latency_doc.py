"""#347: docs/latency.md's measured block and docs/img/latency_tiers.svg are generated from committed runs."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location("make_latency_doc", ROOT / "scripts" / "make_latency_doc.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _jsonl(path: Path, totals, draft=None) -> Path:
    path.write_text("".join(json.dumps({"vad": 0.6, "asr": 0.3, "merge": 0.0, "mt": round(t - 0.9, 3), "total": t,
                                        "t_source_shown": None, "lang": "en", "text": "x",
                                        **({} if draft is None else {"draft": draft})}) + "\n"
                            for t in totals), "utf-8")
    return path


def _pack(m, tmp_path, name, runs, tier, src, preset, totals, date="2026-11-13", draft=None):
    meta = {"date": date, "tier": tier, "hardware": "RTX 3050 8 GB", "model": "large-v3-turbo", "engine": "hymt",
            "preset": preset, "pair": f"{src}->zh-TW"}
    return m.pack(_jsonl(tmp_path / name, totals, draft), meta, runs)


def test_committed_latency_doc_matches_the_committed_runs():
    assert _mod().main(["--check"]) == 0, "run: python scripts/make_latency_doc.py"


def test_tables_and_chart_come_from_the_run_files(tmp_path):
    m = _mod()
    runs, doc, svg = tmp_path / "runs", tmp_path / "latency.md", tmp_path / "chart.svg"
    doc.write_text("# Latency\n\nIntro.\n\n## 2026-11-12 (old smoke)\n\n| a | b |\n", "utf-8")
    totals = [1.0 + i / 10 for i in range(30)]  # 1.0 .. 3.9 s
    _pack(m, tmp_path, "a.jsonl", runs, "mid-gpu", "en", "meeting", totals, draft=0.8)
    _pack(m, tmp_path, "b.jsonl", runs, "cpu-low", "ru", "chat", [2.0, 3.0, 4.0])
    _pack(m, tmp_path, "c.jsonl", runs, "cpu-low", "ru", "chat", [9.0] * 5, date="2026-11-01")  # older: ignored
    assert m.main(["--check"], runs, doc, svg) == 1  # block not written yet
    assert m.main([], runs, doc, svg) == 0
    text = doc.read_text("utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| cpu-low") or line.startswith("| mid-gpu")]
    # tiers in hardware order; nearest-rank p50 / p90 of 1.0..3.9 = 2.4 / 3.6; n < 30 is marked indicative
    assert rows == [
        "| cpu-low | RTX 3050 8 GB | ru->zh-TW | chat | large-v3-turbo / hymt | 2026-11-13 | 3 (indicative) | 3.00 s | "
        "4.00 s | not recorded |",
        "| mid-gpu | RTX 3050 8 GB | en->zh-TW | meeting | large-v3-turbo / hymt | 2026-11-13 | 30 | 2.40 s | 3.60 s | "
        "0.80 s |",
    ]
    assert text.index("<!-- LATENCY:START") < text.index("## 2026-11-12")  # old dated section kept, below
    assert svg.read_text("utf-8").count("<circle") == 33  # one dot per utterance of the newest runs
    assert m.main(["--check"], runs, doc, svg) == 0
    m.main([], runs, doc, svg)
    assert doc.read_text("utf-8") == text  # idempotent: the block is replaced, not appended again
    _pack(m, tmp_path, "d.jsonl", runs, "high", "ja", "meeting", [1.5] * 30)
    assert m.main(["--check"], runs, doc, svg) == 1  # a new run makes the committed doc stale


def test_pack_cli_writes_a_dated_run_file(tmp_path):
    m = _mod()
    runs = tmp_path / "runs"
    src = _jsonl(tmp_path / "lat.jsonl", [1.2, 1.4])
    assert m.main(["pack", str(src), "--tier", "high", "--hardware", "RTX 4090", "--model", "large-v3",
                   "--engine", "hymt", "--preset", "chat", "--pair", "ja->zh-TW", "--date", "2026-11-13"], runs) == 0
    run = json.loads((runs / "2026-11-13_high_ja_chat.json").read_text("utf-8"))
    assert run["hardware"] == "RTX 4090" and [r["total"] for r in run["records"]] == [1.2, 1.4]
    assert "text" not in run["records"][0]  # only timings are committed, not what was said
