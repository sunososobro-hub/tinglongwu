"""#1247: the #894 live-mic check says which clips tlwu missed (file vs mic) and where the longest gap is."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "hw_live_checks.py"


def _load():
    spec = importlib.util.spec_from_file_location("hw_live_checks_1247", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


hl = _load()
REFS = ["the quarterly report shows strong growth in every region",
        "scientists discovered seven species living beneath antarctic glaciers",
        "children painted colourful murals across abandoned warehouse walls"]
SPANS = [[0.0, 10.0], [11.0, 21.0], [22.0, 32.0]]


def test_gap_span_returns_where_the_longest_gap_is():
    assert hl.gap_span([5.0, 30.0], 0.0, 32.0) == (5.0, 30.0)
    assert hl.gap_span([], 0.0, 32.0) == (0.0, 32.0)
    assert hl.gap_span([1.0, 2.0], 0.0, 32.0) == (2.0, 32.0)


def test_clips_in_lists_the_clips_overlapping_a_stretch():
    assert hl.clips_in(SPANS, 5.0, 30.0) == [0, 1, 2]
    assert hl.clips_in(SPANS, 10.5, 21.5) == [1]
    assert hl.clips_in(SPANS, 40.0, 50.0) == []


def test_missed_clips_tells_heard_from_missing_clips():
    rows = [{"src": "The quarterly report shows strong growth in every region."},
            {"src": "Children painted colorful murals across abandoned warehouse walls"}]
    assert hl.missed_clips(rows, REFS) == [1]
    assert hl.missed_clips([], REFS) == [0, 1, 2]
    assert hl.missed_clips([{"src": " ".join(REFS)}], REFS) == []


def test_diagnose_mic_compares_file_and_mic_and_names_the_gap():
    meta = {"speech": [0.0, 32.0], "spans": SPANS, "refs": REFS}
    file_rows = [{"src": r} for r in REFS]
    mic_rows = [{"src": REFS[0]}]
    msg = hl.diagnose_mic(meta, file_rows, mic_rows, [11.0])
    assert "file lines 3, mic lines 1" in msg
    assert "file 0/3 []" in msg and "mic 2/3 [1, 2]" in msg
    assert "longest gap 11-32 s (clips 1..2)" in msg


def test_diagnose_mic_works_with_clips_json_from_before_1247():
    msg = hl.diagnose_mic({"speech": [0.0, 32.0]}, [], [], [])
    assert msg == "file lines 0, mic lines 0; longest gap 0-32 s"


def test_clips_json_has_per_clip_spans_and_refs(tmp_path):
    import json

    import numpy as np
    import soundfile as sf

    data = tmp_path / "data"
    data.mkdir()
    rows = []
    for lang in ("en", "zh"):
        for i in range(20):
            cid = f"{lang}_{i:03d}"
            sf.write(str(data / f"{cid}.wav"), np.full(int(9.0 * hl.SR), 0.1, np.float32), hl.SR)
            rows.append({"id": cid, "lang": lang, "wav": f"{cid}.wav", "ref_text": f"{lang} text {i}"})
    (data / "manifest.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", "utf-8")
    mic = hl.build_clips(data, tmp_path / "clips")["mic_en"]
    assert len(mic["spans"]) == len(mic["refs"]) == len(mic["clips"]) == 31
    assert mic["spans"][0] == [0.0, 9.0] and mic["spans"][1] == [10.0, 19.0]
    assert mic["refs"][20] == "en text 0" and " ".join(mic["refs"]) == mic["ref"]
