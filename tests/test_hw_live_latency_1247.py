"""#1247: the #894 live-mic check prints, for the lines around the longest gap, when their speech ended vs when they
were shown (tlwu --latency-jsonl), so the gap is told apart as processing delay or a missing cut / line."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "hw_live_checks.py"


def _load():
    spec = importlib.util.spec_from_file_location("hw_live_checks_lat_1247", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


hl = _load()
SRCS = ["first line.", "second line.", "third line.", "fourth line."]
LAT = [{"text": s, "total": t, "vad": 0.7, "asr": a} for s, t, a in
       zip(SRCS, [2.0, 2.5, 9.0, 1.5], [1.0, 1.2, 6.5, 0.8])]


def test_match_latency_pairs_records_in_order():
    assert hl.match_latency(SRCS, LAT) == LAT


def test_match_latency_by_text_when_a_record_is_missing():
    got = hl.match_latency(SRCS, [LAT[0], LAT[2], LAT[3]])
    assert got == [LAT[0], None, LAT[2], LAT[3]]


def test_latency_context_shows_speech_end_before_and_after_the_gap():
    offsets = [10.0, 20.0, 34.0, 40.0]
    lines = hl.latency_context(offsets, SRCS, LAT, *hl.gap_span(offsets, 0.0, 45.0), n=1)
    # the gap is 20 -> 34 s; the line after it was shown 9 s after its speech ended (processing delay)
    assert lines == ["shown 20.0s speech end 17.5s vad 0.7s asr 1.2s 'second line.'",
                     "shown 34.0s speech end 25.0s vad 0.7s asr 6.5s 'third line.'"]


def test_latency_context_without_records():
    lines = hl.latency_context([1.0, 15.0], SRCS[:2], [None, None], 1.0, 15.0, n=1)
    assert lines == ["shown 1.0s (no latency record) 'first line.'", "shown 15.0s (no latency record) 'second line.'"]


def test_latency_summary_and_reader(tmp_path):
    p = tmp_path / "lat.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in LAT) + "\nnot json\n", "utf-8")
    rows = hl.read_latency(p)
    assert rows == LAT
    assert hl.latency_summary(rows) == "4 lines, speech end -> shown median 2.2 s, max 9.0 s; ASR max 6.5 s"
    assert hl.latency_summary([]) == "no latency records"
    assert hl.read_latency(tmp_path / "missing.jsonl") == []


def test_live_mic_accepts_latency_jsonl(tmp_path):
    """The flag the check adds to the live run is accepted by tlwu together with --mic (no parser error)."""
    from tinglongwu.cli import parse_args

    a = parse_args(["--mic", "--capture-device", "CABLE Output", "--latency-jsonl", str(tmp_path / "l.jsonl"),
                    "--lang", "en", "--transcript-only"])
    assert a.latency_jsonl == str(tmp_path / "l.jsonl") and a.mic


def test_match_latency_leaves_repeated_text_unmatched():
    srcs = ["Yes.", "other line.", "Yes."]
    lat = [{"text": "other line.", "total": 1.0}, {"text": "Yes.", "total": 2.0}]
    assert hl.match_latency(srcs, lat) == [None, lat[0], None]


def test_latency_context_shows_unknown_stage_times():
    rec = {"text": "a.", "total": 2.0, "vad": None, "asr": "x"}
    assert hl.latency_context([5.0], ["a."], [rec], 5.0, 9.0, n=1) == ["shown 5.0s speech end 3.0s vad ? asr ? 'a.'"]


def test_read_latency_unreadable_path_is_empty(tmp_path):
    assert hl.read_latency(tmp_path) == []  # a directory: read_text raises OSError


def test_latency_summary_unknown_asr_and_u2028_record(tmp_path):
    assert hl.latency_summary([{"total": 2.0, "asr": None}]) == \
        "1 lines, speech end -> shown median 2.0 s, max 2.0 s; ASR max ?"
    p = tmp_path / "lat.jsonl"
    p.write_text(json.dumps({"text": "a\u2028b", "total": 1.0}, ensure_ascii=False) + "\n", "utf-8")
    assert hl.read_latency(p) == [{"text": "a\u2028b", "total": 1.0}]
