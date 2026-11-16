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
    monkeypatch.setenv("HF_TOKEN", "hf_x")  # a token was given: the error is recorded as is
    assert ie.main(["fetch", "--group", "svarah", "--data", str(tmp_path)]) == 0
    assert json.loads((tmp_path / "svarah" / "status.json").read_text()) == {"ok": False, "error": "RuntimeError: gated"}


def test_fetch_svarah_gated_without_token_says_how_to_fix(tmp_path, monkeypatch):
    """#655: run 43521109509 failed with a 401 on the gated set and no token; the status names the HF_TOKEN fix."""
    def boom(data, n):
        raise RuntimeError("401 Client Error. Cannot access gated repo ai4bharat/Svarah. You must be authenticated")

    monkeypatch.setattr(ie, "fetch_svarah", boom)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    assert ie.main(["fetch", "--group", "svarah", "--data", str(tmp_path)]) == 0
    err = json.loads((tmp_path / "svarah" / "status.json").read_text())["error"]
    assert err.startswith("no HF_TOKEN: set the HF_TOKEN repo secret") and "401 Client Error" in err


def test_fetch_svarah_passes_hf_token_to_the_hub(tmp_path, monkeypatch):
    """#655: the token from the environment reaches HfApi and hf_hub_download (the set is gated)."""
    seen = {}

    class Api:
        def __init__(self, token=None):
            seen["api"] = token

        def dataset_info(self, repo):
            return types.SimpleNamespace(sha="0" * 40)

        def list_repo_files(self, repo, repo_type=None, revision=None):
            return []  # no parquet: fetch_svarah stops right after listing

    hub = types.ModuleType("huggingface_hub")
    hub.HfApi = Api
    hub.hf_hub_download = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setitem(sys.modules, "pyarrow", types.ModuleType("pyarrow"))
    pq = types.ModuleType("pyarrow.parquet")
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", pq)
    monkeypatch.setenv("HF_TOKEN", "hf_secret")
    with pytest.raises(RuntimeError, match="no parquet files"):
        ie.fetch_svarah(tmp_path, 3)
    assert seen["api"] == "hf_secret"


def test_fetch_fleurs_failure_still_fails(tmp_path, monkeypatch):
    def boom(data, group, n):
        raise RuntimeError("network")

    monkeypatch.setattr(ie, "fetch_fleurs", boom)
    with pytest.raises(RuntimeError):
        ie.main(["fetch", "--group", "hi", "--data", str(tmp_path)])


def test_load_audio_downmixes_and_resamples_to_16k(tmp_path):
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    pytest.importorskip("scipy")
    t = np.arange(44100) / 44100
    st = np.stack([np.sin(2 * np.pi * 440 * t), np.sin(2 * np.pi * 440 * t)], axis=1).astype("float32")
    sf.write(tmp_path / "a.flac", st, 44100)
    x = ie.load_audio(tmp_path / "a.flac")
    assert x.dtype == np.float32 and x.ndim == 1 and abs(len(x) - 16000) <= 1
    assert 0.6 < float(np.abs(x).max()) < 1.1


# ---------- #655: hinglish group ----------

def _info(rid, tags, gated=False, downloads=0):
    return types.SimpleNamespace(id=rid, tags=tags, gated=gated, downloads=downloads)


def test_hinglish_candidates_keep_only_open_ungated_hindi_english_audio_sets():
    infos = [
        _info("a/hinglish-asr", ["license:cc-by-4.0", "modality:audio"], downloads=5),
        _info("b/code-switch-speech", ["license:apache-2.0", "task_categories:automatic-speech-recognition",
                                       "language:hi", "language:en"], downloads=50),
        _info("h/indic-multi", ["license:cc-by-4.0", "modality:audio", "language:hi", "language:en"],
              downloads=900),  # hi + en tags alone: maybe separate monolingual clips, not code-switched
        _info("j/hindi-tamil-code-switch", ["license:mit", "modality:audio", "language:hi", "language:ta"],
              downloads=900),  # code-switched, but not with English
        _info("i/es-codeswitch", ["license:mit", "modality:audio", "language:es", "language:en"], downloads=900),
        _info("c/hinglish-gated", ["license:cc-by-4.0", "modality:audio"], gated="manual", downloads=900),
        _info("d/hinglish-nc", ["license:cc-by-nc-4.0", "modality:audio"], downloads=900),
        _info("e/hinglish-text", ["license:mit", "modality:text"], downloads=900),
        _info("f/es-en-cs", ["license:mit", "modality:audio", "language:es", "language:en"], downloads=900),
        _info("g/HingLish-tts", ["license:cc0-1.0", "modality:audio"], downloads=5),
        _info("a/hinglish-asr", ["license:cc-by-4.0", "modality:audio"], downloads=5),  # same set from 2nd search
    ]
    assert ie.hinglish_candidates(infos) == ["b/code-switch-speech", "a/hinglish-asr", "g/HingLish-tts"]


def test_open_licence_uses_the_candidate_rule():
    assert ie.open_licence(["license:cc-by-nc-4.0", "license:cc-by-4.0"]) == "cc-by-4.0"
    assert ie.open_licence(["license:cc-by-nc-4.0"]) == "cc-by-nc-4.0"
    assert ie.open_licence(["modality:audio"]) == "unknown"


def _wav(seconds):
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    b = io.BytesIO()
    sf.write(b, np.zeros(int(16000 * seconds), dtype="float32"), 16000, format="WAV")
    return b.getvalue()


class HubApi:
    def __init__(self, files, tags=("license:cc-by-4.0",)):
        self.files, self.tags = files, list(tags)

    def dataset_info(self, repo, revision=None):
        return types.SimpleNamespace(sha="ab" * 20, tags=self.tags)

    def list_repo_files(self, repo, repo_type=None, revision=None):
        return list(self.files)


def test_fetch_hinglish_from_takes_short_transcribed_test_clips(tmp_path):
    pa = pytest.importorskip("pyarrow")
    rows = [({"bytes": _wav(2.0), "path": "x.wav"}, "Mujhe yeh movie bahut pasand aayi"),
            ({"bytes": _wav(30.0), "path": "y.wav"}, "too long"),           # > MAX_CLIP_S: skipped
            ({"bytes": _wav(3.0), "path": "z.flac"}, "  "),                 # no transcript: skipped
            ({"bytes": b"not audio", "path": "w.wav"}, "broken"),           # undecodable: skipped
            ({"bytes": _wav(4.0), "path": "v.wav"}, "Kal meeting hai at ten")]
    table = pa.table({"audio": [a for a, _ in rows], "transcription": [t for _, t in rows]})
    fetched = []

    def download(repo, f, repo_type=None, revision=None):
        fetched.append(f)
        return f

    api = HubApi(["data/train-0.parquet", "data/test-00000.parquet", "README.md"])
    got = ie.fetch_hinglish_from("o/hinglish", None, tmp_path, 10, api, download, lambda f: table)
    assert fetched == ["data/test-00000.parquet"]  # only the test shard is downloaded
    assert [r["ref_text"] for r in got] == ["Mujhe yeh movie bahut pasand aayi", "Kal meeting hai at ten"]
    assert [r["id"] for r in got] == ["hinglish_001", "hinglish_002"]
    assert got[0]["source"] == f"o/hinglish@{'ab' * 6}/row0" and got[1]["source"].endswith("/row4")
    assert got[0]["lang"] == "hi" and got[0]["license"] == "cc-by-4.0"
    assert (tmp_path / "hinglish" / "hinglish_002.wav").read_bytes() == rows[4][0]["bytes"]
    # n caps the clips
    assert len(ie.fetch_hinglish_from("o/hinglish", None, tmp_path, 1, api, download, lambda f: table)) == 1


def test_fetch_hinglish_from_rejects_sets_without_audio_and_text(tmp_path):
    pa = pytest.importorskip("pyarrow")
    with pytest.raises(RuntimeError, match="no parquet files"):
        ie.fetch_hinglish_from("o/x", None, tmp_path, 10, HubApi(["data.zip"]), lambda *a, **k: None, None)
    with pytest.raises(RuntimeError, match="unexpected columns"):
        ie.fetch_hinglish_from("o/x", None, tmp_path, 10, HubApi(["a.parquet"]), lambda *a, **k: "a",
                               lambda f: pa.table({"text": ["hi"]}))


def test_pick_columns_prefers_the_exact_audio_column():
    assert ie.pick_columns(["audio_id", "audio", "text"]) == ("audio", "text")
    assert ie.pick_columns(["audio_filepath", "transcription"]) == ("audio_filepath", "transcription")
    assert ie.pick_columns(["path", "label"]) == (None, None)


@pytest.mark.parametrize("tags,gated,why", [(["license:cc-by-nc-4.0"], False, "not in OPEN_LICENSES"),
                                            ([], False, "'unknown' is not in OPEN_LICENSES"),
                                            (["license:cc-by-4.0"], "manual", "gated set")])
def test_fetch_hinglish_from_rejects_closed_sets_before_downloading(tmp_path, tags, gated, why):
    """A HINGLISH_DATASET override gets the same open-set rule as the search, and nothing is downloaded."""
    api = HubApi(["test.parquet"], tags=tags)
    api.dataset_info = lambda repo, revision=None: types.SimpleNamespace(sha="ab" * 20, tags=tags, gated=gated)

    def download(*a, **k):
        raise AssertionError("downloaded a closed set")

    with pytest.raises(RuntimeError, match=why):
        ie.fetch_hinglish_from("o/x", None, tmp_path, 10, api, download, None)


def test_fetch_hinglish_failure_is_recorded_not_raised(tmp_path, monkeypatch):
    def boom(data, n):
        raise RuntimeError("no open Hinglish speech set")

    monkeypatch.setattr(ie, "fetch_hinglish", boom)
    assert ie.main(["fetch", "--group", "hinglish", "--data", str(tmp_path)]) == 0
    st = json.loads((tmp_path / "hinglish" / "status.json").read_text())
    assert st == {"ok": False, "error": "RuntimeError: no open Hinglish speech set"}


def test_fetch_hinglish_caps_at_10_clips_and_records_the_source(tmp_path, monkeypatch):
    seen = {}

    def fake(data, n):
        seen["n"] = n
        return [{"id": "hinglish_001", "wav": "hinglish_001.wav", "lang": "hi", "ref_text": "kya haal hai",
                 "source": "o/hinglish@abababababab/row3", "license": "cc-by-4.0"}]

    monkeypatch.setattr(ie, "fetch_hinglish", fake)
    assert ie.main(["fetch", "--group", "hinglish", "--data", str(tmp_path), "--n", "50"]) == 0
    assert seen["n"] == 10
    st = json.loads((tmp_path / "hinglish" / "status.json").read_text())
    assert st == {"ok": True, "clips": 1, "source": "o/hinglish@abababababab", "license": "cc-by-4.0"}


def test_run_hinglish_translates_like_hindi():
    tr = FakeTr()
    out = ie.run_group([{"id": "hinglish_001", "ref_text": "kya haal hai"}], "hinglish", FakeAsr(["क्या हाल है"]),
                       tr, lambda r: [0.0] * 16000, clock())
    assert out[0]["zh_direct"] == "zh(क्या हाल है)" and out[0]["zh_pivot"] == "zh(en(क्या हाल है))"
    assert out[0]["zh_gold"] == "zh(kya haal hai)" and ("kya haal hai", "hi", "zh") in tr.calls


def test_report_has_hinglish_rows_and_source(tmp_path, exact_chrf, capsys):
    hg = [{"ref_text": "kal meeting hai", "asr": "kal meeting hai", "asr_s": 1.0, "audio_s": 2.0,
           "zh_direct": "明天開會", "zh_direct_s": 0.3, "zh_pivot": "明天有會", "zh_pivot_s": 0.6,
           "zh_gold": "明天開會", "zh_gold_s": 0.3}]
    _write(tmp_path, "hinglish", hg)
    (tmp_path / "hinglish.status.json").write_text(json.dumps(
        {"ok": True, "clips": 1, "source": "o/hinglish@abababababab", "license": "cc-by-4.0"}))
    assert ie.main(["report", str(tmp_path)]) == 0
    s = json.loads((tmp_path / "summary.json").read_text("utf-8"))
    by = {(r["group"], r["route"]): r for r in s["rows"]}
    assert set(by) == {("hinglish", "zh_direct"), ("hinglish", "zh_pivot")}
    assert by[("hinglish", "zh_direct")]["chrf_vs_gold_mt"] == 100.0 and by[("hinglish", "zh_pivot")]["chrf_vs_gold_mt"] == 0.0
    assert by[("hinglish", "zh_direct")]["chrf_vs_human_zh"] is None and by[("hinglish", "zh_direct")]["asr_wer"] == 0.0
    assert s["sources"] == {"hinglish": "o/hinglish@abababababab (licence cc-by-4.0)"}
    out = capsys.readouterr().out
    assert "Hinglish (Hindi-English code-switch) -> zh, direct | 1 |" in out
    assert "hinglish source: o/hinglish@abababababab (licence cc-by-4.0)" in out
