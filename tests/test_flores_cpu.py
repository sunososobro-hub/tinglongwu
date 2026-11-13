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
