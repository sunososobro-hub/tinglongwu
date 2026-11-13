"""#620: Indian-English vs Hindi -> Chinese eval (scripts/bench/india_eval.py).
No model, network or sacrebleu needed: ASR, translator and chrF are stubs; the tar archive is built in memory."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tarfile
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("bench_india_eval_620", ROOT / "scripts" / "bench" / "india_eval.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ie = _load()


def tsv(rows):
    """rows: (flores_id, file, text, seconds) -> FLEURS test.tsv text (7 columns, samples in col 5)."""
    return "".join(f"{i}\t{f}\t{t}\t{t.lower()}\tx\t{int(s * 16000)}\tMALE\n" for i, f, t, s in rows)


TSVS = {
    "en": ie.read_tsv(tsv([(7, "e7a.wav", "Seven.", 5), (7, "e7b.wav", "Seven again.", 5), (3, "e3.wav", "Three.", 30),
                           (5, "e5.wav", "Five.", 4), (9, "e9.wav", "Nine.", 6), (4, "e4.wav", "Four.", 3)])),
    "hi": ie.read_tsv(tsv([(5, "h5.wav", "पाँच।", 6), (7, "h7.wav", "सात।", 8), (3, "h3.wav", "तीन।", 5),
                           (4, "h4long.wav", "चार।", 25)])),
    "zh": ie.read_tsv(tsv([(7, "z7.wav", "七。", 4), (5, "z5.wav", "五。", 4), (3, "z3.wav", "三。", 4),
                           (4, "z4.wav", "四。", 4)])),
}


def test_select_same_flores_sentences_in_en_and_hi():
    rows = ie.select_fleurs(TSVS, 10)
    # 7 (first en clip only), 5; 3 dropped (en clip 30 s), 9 dropped (no hi), 4 dropped (hi clip 25 s)
    assert [r["flores_id"] for r in rows] == [7, 5]
    assert rows[0]["en"] == {"file": "e7a.wav", "text": "Seven."}
    assert rows[0]["hi"] == {"file": "h7.wav", "text": "सात।"}
    assert rows[1]["ref_zh"] == "五。"
    assert [r["flores_id"] for r in ie.select_fleurs(TSVS, 1)] == [7]


def test_spread_covers_the_whole_set():
    assert ie.spread(1000, 4) == [0, 250, 500, 750]
    assert ie.spread(3, 10) == [0, 1, 2]


def _tar(members):
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w:gz") as tf:
        for name, data in members:
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    b.seek(0)
    return b


def test_extract_members_by_base_name_only():
    got = ie.extract_members(_tar([("test/a.wav", b"A"), ("../../evil/b.wav", b"B"), ("test/c.wav", b"C")]),
                             {"b.wav": 1, "c.wav": 1})
    assert got == {"b.wav": b"B", "c.wav": b"C"}


class FakeAsr:
    def __init__(self, texts):
        self.texts = texts

    def transcribe(self, x):
        t = self.texts.pop(0)
        return types.SimpleNamespace(text=f" {t} ") if t is not None else None


class FakeTr:
    def __init__(self):
        self.calls = []

    def translate(self, text, src, tgt):
        self.calls.append((text, src, tgt))
        return types.SimpleNamespace(text=f"{tgt}({text})")


def clock():
    t = [0.0]

    def c():
        t[0] += 0.5
        return t[0]
    return c


def test_run_hindi_direct_pivot_and_gold_routes():
    rows = [{"id": "hi_001", "ref_text": "सात।", "ref_zh": "七。"}, {"id": "hi_002", "ref_text": "पाँच।", "ref_zh": "五。"}]
    tr = FakeTr()
    out = ie.run_group(rows, "hi", FakeAsr(["सात", None]), tr, lambda r: [0.0] * 32000, clock())
    assert out[0]["asr"] == "सात" and out[0]["audio_s"] == 2.0 and out[0]["asr_s"] == 0.5
    assert out[0]["zh_direct"] == "zh(सात)"
    assert out[0]["zh_pivot"] == "zh(en(सात))"  # hi -> en, then en -> zh
    assert out[0]["zh_gold"] == "zh(सात।)"
    assert ("सात", "hi", "en") in tr.calls and ("en(सात)", "en", "zh") in tr.calls
    # no speech: empty ASR text is not sent to the translator, gold still is
    assert out[1]["asr"] == "" and out[1]["zh_direct"] == "" and out[1]["zh_pivot"] == ""
    assert out[1]["zh_gold"] == "zh(पाँच।)"


def test_run_english_has_no_pivot():
    out = ie.run_group([{"id": "en_001", "ref_text": "Seven."}], "en", FakeAsr(["seven"]), FakeTr(),
                       lambda r: [0.0] * 16000, clock())
    assert set(k for k in out[0] if k.startswith("zh_") and not k.endswith("_s")) == {"zh_direct", "zh_gold"}


@pytest.fixture
def exact_chrf(monkeypatch):
    """chrF stub: 100 x share of hypotheses equal to their reference (enough to check what is compared to what)."""

    class CHRF:
        def corpus_score(self, hyps, refs):
            return types.SimpleNamespace(score=100.0 * sum(h == r for h, r in zip(hyps, refs[0])) / len(hyps))

    mod = types.ModuleType("sacrebleu.metrics")
    mod.CHRF = CHRF
    monkeypatch.setitem(sys.modules, "sacrebleu", types.ModuleType("sacrebleu"))
    monkeypatch.setitem(sys.modules, "sacrebleu.metrics", mod)


def _write(d: Path, group, rows, ok=True, error=None):
    (d / f"{group}.status.json").write_text(json.dumps({"ok": ok, "error": error} if not ok else {"ok": True}))
    if ok:
        (d / f"{group}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")


def test_report_scores_each_route_and_lists_missing_groups(tmp_path, exact_chrf, capsys):
    hi = [{"ref_text": "एक दो", "asr": "एक तीन", "asr_s": 1.0, "audio_s": 4.0, "ref_zh": "一二",
           "zh_direct": "一二", "zh_direct_s": 0.4, "zh_pivot": "一三", "zh_pivot_s": 0.8, "zh_gold": "一二",
           "zh_gold_s": 0.4},
          {"ref_text": "चार", "asr": "चार", "asr_s": 3.0, "audio_s": 4.0, "ref_zh": "四",
           "zh_direct": "肆", "zh_direct_s": 0.6, "zh_pivot": "四", "zh_pivot_s": 1.0, "zh_gold": "四",
           "zh_gold_s": 0.4}]
    en = [{"ref_text": "One two.", "asr": "one two", "asr_s": 1.0, "audio_s": 2.0, "ref_zh": "一二",
           "zh_direct": "一二", "zh_direct_s": 0.2, "zh_gold": "一二", "zh_gold_s": 0.2}]
    _write(tmp_path, "hi", hi)
    _write(tmp_path, "en", en)
    _write(tmp_path, "svarah", [], ok=False, error="GatedRepoError: 401 | access")
    assert ie.main(["report", str(tmp_path)]) == 0
    s = json.loads((tmp_path / "summary.json").read_text("utf-8"))
    by = {(r["group"], r["route"]): r for r in s["rows"]}
    assert set(by) == {("en", "zh_direct"), ("hi", "zh_direct"), ("hi", "zh_pivot")}
    d, p = by[("hi", "zh_direct")], by[("hi", "zh_pivot")]
    assert d["asr_wer"] == pytest.approx(1 / 3, abs=1e-4)  # one substitution in three Hindi words (danda dropped)
    assert d["asr_rtf"] == 0.5 and d["asr_median_s"] == 2.0
    assert d["chrf_vs_human_zh"] == 50.0 and p["chrf_vs_human_zh"] == 50.0
    assert d["chrf_vs_gold_mt"] == 50.0 and p["chrf_vs_gold_mt"] == 50.0
    assert d["mt_median_s"] == 0.5 and p["mt_median_s"] == 0.9
    assert d["chrf_gold_text_vs_human_zh"] == 100.0
    assert by[("en", "zh_direct")]["asr_wer"] == 0.0  # case and punctuation do not count
    assert "svarah" in s["missing"] and "GatedRepoError" in s["missing"]["svarah"]
    out = capsys.readouterr().out
    assert "FLEURS hi_in (Hindi) -> en -> zh, pivot" in out and "svarah: not measured (GatedRepoError: 401 / access)" in out


def test_report_svarah_has_no_human_zh_reference(tmp_path, exact_chrf):
    sv = [{"ref_text": "hello there", "asr": "hello their", "asr_s": 1.0, "audio_s": 2.0,
           "zh_direct": "你好那裡", "zh_direct_s": 0.3, "zh_gold": "你好", "zh_gold_s": 0.3}]
    _write(tmp_path, "svarah", sv)
    ie.main(["report", str(tmp_path)])
    (row,) = json.loads((tmp_path / "summary.json").read_text("utf-8"))["rows"]
    assert row["chrf_vs_human_zh"] is None and row["chrf_vs_gold_mt"] == 0.0 and row["asr_wer"] == 0.5


def test_fetch_svarah_failure_is_recorded_not_raised(tmp_path, monkeypatch):
    def boom(data, n):
        raise RuntimeError("gated")

    monkeypatch.setattr(ie, "fetch_svarah", boom)
    assert ie.main(["fetch", "--group", "svarah", "--data", str(tmp_path)]) == 0
    assert json.loads((tmp_path / "svarah" / "status.json").read_text()) == {"ok": False, "error": "RuntimeError: gated"}


def test_fetch_fleurs_failure_still_fails(tmp_path, monkeypatch):
    def boom(data, group, n):
        raise RuntimeError("network")

    monkeypatch.setattr(ie, "fetch_fleurs", boom)
    with pytest.raises(RuntimeError):
        ie.main(["fetch", "--group", "hi", "--data", str(tmp_path)])
