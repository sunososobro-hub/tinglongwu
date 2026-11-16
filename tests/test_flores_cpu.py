"""#606: the CPU FLORES-200 devtest runner (scripts/bench/flores_cpu.py) and `fetch_data.py --fleurs ""`.
No model, network or sacrebleu/COMET install needed: the translator, metric and COMET model are stubs."""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(f"bench_{name}_606", ROOT / "scripts" / "bench" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class FakeTr:
    def __init__(self):
        self.calls = []

    def translate(self, text, src, tgt):
        self.calls.append((text, src, tgt))
        return types.SimpleNamespace(text=f" zh:{text} ")


class FakeScore:
    def __init__(self, score):
        self.score = score


class FakeCHRF:
    def corpus_score(self, hyp, refs):
        return FakeScore(50.0 + len(hyp))

    def sentence_score(self, hyp, refs):
        return FakeScore(10.0 * len(hyp))


class FakeComet:
    def __init__(self):
        self.kw = None

    def predict(self, data, **kw):
        self.kw = kw
        return types.SimpleNamespace(system_score=0.8, scores=[0.7 + 0.1 * i for i in range(len(data))])


@pytest.fixture
def flores(tmp_path, monkeypatch):
    d = tmp_path / "flores200_dataset" / "devtest"
    d.mkdir(parents=True)
    (d / "rus_Cyrl.devtest").write_text("один\nдва\nтри\n", "utf-8")
    (d / "zho_Hant.devtest").write_text("一\n二\n三\n", "utf-8")
    mod = types.ModuleType("sacrebleu.metrics")
    mod.CHRF = FakeCHRF
    monkeypatch.setitem(sys.modules, "sacrebleu", types.ModuleType("sacrebleu"))
    monkeypatch.setitem(sys.modules, "sacrebleu.metrics", mod)
    return tmp_path / "flores200_dataset"


def test_full_run_writes_per_sentence_json_and_summary(flores, tmp_path):
    m = _load("flores_cpu")
    tr, comet = FakeTr(), FakeComet()
    out = tmp_path / "out"
    assert m.main(["--data", str(flores), "--models-dir", "x", "--src", "rus_Cyrl", "--out", str(out)],
                  make=lambda _d: tr, load_comet=lambda _n: comet) == 0
    assert tr.calls == [("один", "ru", "zh"), ("два", "ru", "zh"), ("три", "ru", "zh")]
    rows = [json.loads(x) for x in (out / "rus_Cyrl.jsonl").read_text("utf-8").splitlines()]
    assert [r["hyp"] for r in rows] == ["zh:один", "zh:два", "zh:три"]
    assert [r["ref"] for r in rows] == ["一", "二", "三"]
    assert all("chrf" in r and "comet" in r and r["sec"] >= 0 for r in rows)
    s = json.loads((out / "rus_Cyrl.summary.json").read_text("utf-8"))
    assert s["n"] == 3 and s["chrF"] == 53.0 and s["COMET"] == 0.8 and s["pair"] == "rus_Cyrl->zho_Hant"
    assert s["median_s"] is not None and s["cpu"]
    assert comet.kw["gpus"] == 0  # CPU runner: COMET must not ask for a GPU


def test_rerun_resumes_and_limit(flores, tmp_path):
    m = _load("flores_cpu")
    out = tmp_path / "out"
    out.mkdir()
    (out / "rus_Cyrl.jsonl").write_text(json.dumps({"i": 0, "src": "один", "ref": "一", "hyp": "一", "sec": 1.5})
                                        + "\n", "utf-8")
    tr = FakeTr()
    m.main(["--data", str(flores), "--models-dir", "x", "--src", "rus_Cyrl", "--out", str(out), "--limit", "2",
            "--no-comet"], make=lambda _d: tr)
    assert tr.calls == [("два", "ru", "zh")]  # sentence 0 came from the earlier run, sentence 2 is past --limit
    s = json.loads((out / "rus_Cyrl.summary.json").read_text("utf-8"))
    assert s["n"] == 2 and "COMET" not in s


def test_score_only_never_builds_the_translator(flores, tmp_path):
    m = _load("flores_cpu")
    out = tmp_path / "out"
    m.main(["--data", str(flores), "--models-dir", "x", "--src", "rus_Cyrl", "--out", str(out), "--no-comet"],
           make=lambda _d: FakeTr())
    first = json.loads((out / "rus_Cyrl.summary.json").read_text("utf-8"))

    def boom(_d):
        raise AssertionError("--score-only must not load Hy-MT2")

    comet = FakeComet()
    assert m.main(["--data", str(flores), "--src", "rus_Cyrl", "--out", str(out), "--score-only"],
                  make=boom, load_comet=lambda _n: comet) == 0
    s = json.loads((out / "rus_Cyrl.summary.json").read_text("utf-8"))
    assert s["COMET"] == 0.8 and s["translate_wall_s"] == first["translate_wall_s"] and s["cpu"] == first["cpu"]
    (out / "rus_Cyrl.jsonl").write_text("", "utf-8")  # incomplete run: --score-only refuses instead of translating
    with pytest.raises(SystemExit, match="missing"):
        m.main(["--data", str(flores), "--src", "rus_Cyrl", "--out", str(out), "--score-only"], make=boom,
               load_comet=lambda _n: comet)


def test_line_count_mismatch_stops(flores):
    (flores / "devtest" / "zho_Hant.devtest").write_text("一\n二\n", "utf-8")
    m = _load("flores_cpu")
    with pytest.raises(SystemExit, match="3 lines"):
        m.load_pair(flores, "rus_Cyrl")


def test_fetch_data_empty_fleurs_fetches_flores_only(tmp_path, monkeypatch, capsys):
    m = _load("fetch_data")
    (tmp_path / "flores200_dataset").mkdir()  # already downloaded: no network at all
    called = []
    monkeypatch.setattr(m, "fetch_fleurs", lambda code, out: called.append(code))
    assert m.main(["--out", str(tmp_path), "--fleurs", ""]) == 0
    assert called == []


NEW_656 = {"kor_Hang": "ko", "vie_Latn": "vi", "tha_Thai": "th", "ind_Latn": "id", "spa_Latn": "es",
           "deu_Latn": "de", "fra_Latn": "fr"}


@pytest.mark.parametrize("src,code", sorted(NEW_656.items()))
def test_656_new_directions_translate_with_the_hymt_language_code(flores, tmp_path, src, code):
    (flores / "devtest" / f"{src}.devtest").write_text("a\nb\nc\n", "utf-8")
    m = _load("flores_cpu")
    tr = FakeTr()
    out = tmp_path / "out"
    assert m.main(["--data", str(flores), "--models-dir", "x", "--src", src, "--out", str(out), "--no-comet"],
                  make=lambda _d: tr) == 0
    assert [c[1:] for c in tr.calls] == [(code, "zh")] * 3
    assert json.loads((out / f"{src}.summary.json").read_text("utf-8"))["pair"] == f"{src}->zho_Hant"
    sys.path.insert(0, str(ROOT))
    from tinglongwu.hymt import EN_NAMES, ZH_NAMES  # Hy-MT2 prompt must name the source language

    assert code in ZH_NAMES and code in EN_NAMES


def test_656_matrix_validates_the_langs_input():
    m = _load("flores_cpu")
    assert m.matrix(" kor_Hang, vie_Latn,kor_Hang ,") == ["kor_Hang", "vie_Latn"]
    for bad in ("", "kor_Hang,x;rm -rf /", "zho_Hant"):
        with pytest.raises(SystemExit):
            m.matrix(bad)


def test_656_matrix_cli_prints_json(tmp_path):
    import subprocess

    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "bench" / "flores_cpu.py"), "--matrix",
                        "rus_Cyrl,fra_Latn"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and json.loads(r.stdout) == ["rus_Cyrl", "fra_Latn"]


class FffdTr(FakeTr):
    """#107/#632: a translator whose second sentence comes back with a broken UTF-8 byte (U+FFFD)."""

    def translate(self, text, src, tgt):
        self.calls.append((text, src, tgt))
        return types.SimpleNamespace(text="�二" if text == "два" else f"zh:{text}")


def test_107_temp_sets_hymt_temperature_and_counts_fffd_lines(flores, tmp_path, monkeypatch):
    monkeypatch.setenv("TLWU_HYMT_TEMP", "0.7")  # registered first, so teardown undoes main()'s --temp write
    m = _load("flores_cpu")
    seen = []

    def make(_d):
        sys.path.insert(0, str(ROOT))
        from tinglongwu.hymt import sampling

        seen.append(sampling()["temperature"])  # what the real HyMtTranslator would pass to llama.cpp
        return FffdTr()

    out = tmp_path / "out"
    assert m.main(["--data", str(flores), "--models-dir", "x", "--src", "rus_Cyrl", "--out", str(out),
                   "--no-comet", "--temp", "0.2"], make=make) == 0
    assert seen == [0.2]
    s = json.loads((out / "rus_Cyrl.summary.json").read_text("utf-8"))
    assert s["temperature"] == 0.2 and s["fffd_lines"] == 1

    def boom(_d):
        raise AssertionError("--score-only must not load Hy-MT2")

    monkeypatch.setenv("TLWU_HYMT_TEMP", "0.9")  # COMET venv: the summary keeps the translate run's temperature
    assert m.main(["--data", str(flores), "--src", "rus_Cyrl", "--out", str(out), "--score-only"],
                  make=boom, load_comet=lambda _n: FakeComet()) == 0
    s = json.loads((out / "rus_Cyrl.summary.json").read_text("utf-8"))
    assert s["temperature"] == 0.2 and s["fffd_lines"] == 1 and s["COMET"] == 0.8


def test_107_without_temp_the_summary_records_the_shipped_default(flores, tmp_path, monkeypatch):
    monkeypatch.delenv("TLWU_HYMT_TEMP", raising=False)
    m = _load("flores_cpu")
    sys.path.insert(0, str(ROOT))
    from tinglongwu.hymt import SAMPLING

    out = tmp_path / "out"
    m.main(["--data", str(flores), "--models-dir", "x", "--src", "rus_Cyrl", "--out", str(out), "--no-comet"],
           make=lambda _d: FakeTr())
    s = json.loads((out / "rus_Cyrl.summary.json").read_text("utf-8"))
    assert s["temperature"] == SAMPLING["temperature"] and s["fffd_lines"] == 0


def test_107_temp_out_of_range_is_rejected(flores, tmp_path):
    m = _load("flores_cpu")
    with pytest.raises(SystemExit):
        m.main(["--data", str(flores), "--models-dir", "x", "--src", "rus_Cyrl", "--out", str(tmp_path / "o"),
                "--no-comet", "--temp", "5"], make=lambda _d: FakeTr())


def test_107_temps_validates_the_workflow_input():
    m = _load("flores_cpu")
    assert m.temps(" 0.7,0.2, 0,0.2,") == ["0.7", "0.2", "0"]
    for bad in ("", "0.7;rm -rf /", "hot", "-1", "3"):
        with pytest.raises(SystemExit):
            m.temps(bad)


def test_107_temps_cli_prints_json():
    import subprocess

    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "bench" / "flores_cpu.py"), "--temps", "0.7,0.2,0"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and json.loads(r.stdout) == ["0.7", "0.2", "0"]


def test_107_resume_at_another_temperature_stops_instead_of_mixing(flores, tmp_path, monkeypatch):
    monkeypatch.setenv("TLWU_HYMT_TEMP", "0.7")
    m = _load("flores_cpu")
    out = tmp_path / "out"
    base = ["--data", str(flores), "--models-dir", "x", "--src", "rus_Cyrl", "--out", str(out), "--no-comet"]
    m.main(base + ["--limit", "1", "--temp", "0.7"], make=lambda _d: FakeTr())
    assert json.loads((out / "rus_Cyrl.jsonl").read_text("utf-8").splitlines()[0])["temp"] == 0.7
    tr = FakeTr()
    with pytest.raises(SystemExit, match="temperature 0.7, not 0.0"):
        m.main(base + ["--temp", "0"], make=lambda _d: tr)
    assert tr.calls == []
    tr = FakeTr()
    m.main(base + ["--temp", "0.7"], make=lambda _d: tr)  # same temperature: resumes as before
    assert [c[0] for c in tr.calls] == ["два", "три"]


# ---------------------------------------------------------------- #1305: 6x6 language-pair matrix
class EchoTr(FakeTr):
    def __init__(self, variant="tw"):
        super().__init__()
        self.variant = variant

    def translate(self, text, src, tgt):
        self.calls.append((text, src, tgt))
        return types.SimpleNamespace(text=f"{tgt}:{text}")


def test_1305_any_target_uses_its_reference_and_language_code(flores, tmp_path):
    (flores / "devtest" / "eng_Latn.devtest").write_text("one\ntwo\nthree\n", "utf-8")
    m = _load("flores_cpu")
    tr, made = EchoTr(), []

    def make(_d, **kw):
        made.append(kw)
        return tr

    out = tmp_path / "out"
    assert m.main(["--data", str(flores), "--models-dir", "x", "--src", "rus_Cyrl", "--tgt", "eng_Latn",
                   "--out", str(out), "--no-comet", "--limit", "2"], make=make) == 0
    assert tr.calls == [("один", "ru", "en"), ("два", "ru", "en")] and made == [{}]
    rows = [json.loads(x) for x in (out / "rus_Cyrl-eng_Latn.jsonl").read_text("utf-8").splitlines()]
    assert [r["ref"] for r in rows] == ["one", "two"]  # scored against the English FLORES side, not zho_Hant
    s = json.loads((out / "rus_Cyrl-eng_Latn.summary.json").read_text("utf-8"))
    assert s["pair"] == "rus_Cyrl->eng_Latn" and s["n"] == 2
    assert not (out / "rus_Cyrl.jsonl").exists()  # the classic X -> zh-Hant file name is left alone


def test_1305_zh_sources_and_simplified_target(flores, tmp_path):
    (flores / "devtest" / "zho_Hans.devtest").write_text("一\n二\n三\n", "utf-8")
    m = _load("flores_cpu")
    tr, made = EchoTr(), []

    def make(_d, **kw):
        made.append(kw)
        return tr

    out = tmp_path / "out"
    m.main(["--data", str(flores), "--models-dir", "x", "--src", "zho_Hant", "--tgt", "rus_Cyrl", "--out", str(out),
            "--no-comet"], make=make)
    assert [c[1:] for c in tr.calls] == [("zh", "ru")] * 3
    tr.calls.clear()
    m.main(["--data", str(flores), "--models-dir", "x", "--src", "rus_Cyrl", "--tgt", "zho_Hans", "--out", str(out),
            "--no-comet"], make=make)
    assert [c[1:] for c in tr.calls] == [("ru", "zh")] * 3 and made[-1] == {"zh_variant": "cn"}


def test_1305_zh_script_pair_is_opencc_not_mt(flores, tmp_path):
    (flores / "devtest" / "zho_Hans.devtest").write_text("一\n二\n三\n", "utf-8")
    m = _load("flores_cpu")

    def boom(*_a, **_k):
        raise AssertionError("zh-Hans -> zh-Hant must not load Hy-MT2")

    out = tmp_path / "out"
    seen = []
    orig = m.ScriptConvert.__init__

    def init(self, variant, convert=None):
        orig(self, variant, convert=lambda t, v: seen.append(v) or f"[{v}]{t}")

    m.ScriptConvert.__init__ = init
    assert m.main(["--data", str(flores), "--src", "zho_Hans", "--tgt", "zho_Hant", "--out", str(out),
                   "--no-comet", "--models-dir", "x"], make=boom) == 0
    assert seen == ["tw"] * 3
    s = json.loads((out / "zho_Hans.summary.json").read_text("utf-8"))  # X -> zh-Hant keeps the classic name
    assert s["engine"].startswith("OpenCC") and s["pair"] == "zho_Hans->zho_Hant"


def test_1305_same_source_and_target_is_rejected(flores, tmp_path):
    m = _load("flores_cpu")
    with pytest.raises(SystemExit):
        m.main(["--data", str(flores), "--models-dir", "x", "--src", "rus_Cyrl", "--tgt", "rus_Cyrl",
                "--out", str(tmp_path / "o"), "--no-comet"], make=lambda _d: FakeTr())


def _summ(a, b, comet, chrf=40.0, n=200):
    return {"pair": f"{a}->{b}", "COMET": comet, "chrF": chrf, "n": n}


def test_1305_table_is_6x6_with_weak_cells(tmp_path):
    m = _load("flores_cpu")
    langs = m.MATRIX6
    sums = [_summ(a, b, 0.85) for a in langs for b in langs if a != b]
    sums = [s for s in sums if s["pair"] != "kor_Hang->rus_Cyrl"]  # one missing cell
    for s in sums:
        if s["pair"] == "rus_Cyrl->zho_Hant":
            s["COMET"] = 0.80  # weakest shipped direction -> the weak line
        if s["pair"] == "jpn_Jpan->kor_Hang":
            s["COMET"] = 0.79
    for i, s in enumerate(sums):
        (tmp_path / f"a{i}").mkdir()
        (tmp_path / f"a{i}" / "x.summary.json").write_text(json.dumps(s), "utf-8")
    t = m.table(m.read_summaries([str(tmp_path)]))
    lines = t.splitlines()
    assert lines[0].split("|")[2:8] == [" en ", " ru ", " zh-Hans ", " zh-Hant ", " ja ", " ko "]
    body = {ln.split("|")[1].strip(): [c.strip() for c in ln.split("|")[2:8]] for ln in lines[2:8]}
    assert set(body) == {"**en**", "**ru**", "**zh-Hans**", "**zh-Hant**", "**ja**", "**ko**"}
    assert body["**en**"][0] == "" and body["**en**"][1] == "0.850 / 40.0"
    assert body["**ja**"][5] == "0.790 / 40.0 (weak)"
    assert body["**ru**"][3] == "0.800 / 40.0"  # the weakest shipped cell itself is not below itself
    assert body["**ko**"][1] == "-"
    assert "29/30 cells" in t and "(0.800)" in t


def test_1305_matrix6_cli_prints_the_six_languages():
    import subprocess

    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "bench" / "flores_cpu.py"), "--matrix6"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and json.loads(r.stdout) == ["eng_Latn", "rus_Cyrl", "zho_Hans", "zho_Hant",
                                                          "jpn_Jpan", "kor_Hang"]
