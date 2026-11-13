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
    run = json.loads((runs / "2026-11-13_high_ja_zh-TW_chat_hymt.json").read_text("utf-8"))
    assert run["hardware"] == "RTX 4090" and [r["total"] for r in run["records"]] == [1.2, 1.4]
    assert "text" not in run["records"][0]  # only timings are committed, not what was said



def test_pack_refuses_to_overwrite_and_drops_records_without_total(tmp_path):
    m = _mod()
    runs = tmp_path / "runs"
    first = _pack(m, tmp_path, "lat.jsonl", runs, "high", "ja", "chat", [1.2, 1.4])
    assert _pack(m, tmp_path, "ru.jsonl", runs, "high", "ru", "chat", [2.0]) != first  # other pair, other file
    try:
        _pack(m, tmp_path, "again.jsonl", runs, "high", "ja", "chat", [9.9])
    except FileExistsError:
        pass
    else:
        raise AssertionError("a second session with the same name must not replace the first")
    assert [r["total"] for r in json.loads(first.read_text("utf-8"))["records"]] == [1.2, 1.4]
    meta = {"date": "2026-11-14", "tier": "high", "hardware": "x", "model": "m", "engine": "hymt",
            "preset": "chat", "pair": "ja->zh-TW"}
    mixed = _jsonl(tmp_path / "mixed.jsonl", [1.5])
    with mixed.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"vad": 0.5, "asr": 0.2, "total": None}) + "\n")  # no end-to-end figure
    out = m.pack(mixed, meta, runs)
    assert [r["total"] for r in json.loads(out.read_text("utf-8"))["records"]] == [1.5]
    m.cells(m.load_runs(runs))  # every committed record has `total`, so regenerating cannot hit a KeyError
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"vad": 0.5, "total": None}) + "\n", "utf-8")
    try:
        m.pack(bad, {**meta, "date": "2026-11-15"}, runs)
    except ValueError:
        pass
    else:
        raise AssertionError("a file with no usable records must be rejected")


def test_unknown_argument_is_an_error_not_a_rewrite(tmp_path):
    m = _mod()
    doc, svg = tmp_path / "latency.md", tmp_path / "chart.svg"
    doc.write_text("# Latency\n", "utf-8")
    assert m.main(["--chek"], tmp_path / "runs", doc, svg) == 2
    assert doc.read_text("utf-8") == "# Latency\n" and not svg.exists()
