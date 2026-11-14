"""#827: `python -m tlwu_eval --text-cases FILE` scores text cases with an MT-only pass."""
import json
from types import SimpleNamespace

from tlwu_eval.__main__ import main
from tlwu_eval.runner import Engines

CASES = [
    {"id": "issue-1", "pair": "ja->zh", "src": "ja one", "ref": "好的", "tlwu": "壞的", "target": "zh-TW"},
    {"id": "issue-2", "pair": "ja->zh", "src": "ja two", "ref": "你好", "tlwu": "你好"},
    {"id": "issue-3", "pair": "en->zh", "src": "hello", "ref": "哈囉", "tlwu": "嗨"},
]
OUT = {"ja one": "好的", "ja two": "你好", "hello": "嗨"}


class FakeMT:
    def __init__(self):
        self.calls = []

    def translate(self, text, src, tgt):
        self.calls.append((text, src, tgt))
        return SimpleNamespace(text=OUT[text])


def _engines(mt):
    return Engines(load_asr=lambda lang: (_ for _ in ()).throw(AssertionError("ASR must not load")), load_mt=lambda: mt)


def _write(tmp_path, cases):
    f = tmp_path / "user_cases.jsonl"
    f.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), "utf-8")
    return f


def test_report_has_per_pair_and_per_case_rows(tmp_path, capsys):
    mt, out = FakeMT(), tmp_path / "r.json"
    code = main(["--text-cases", str(_write(tmp_path, CASES)), "--out", str(out)], engines=_engines(mt))
    assert code == 0
    assert mt.calls == [("ja one", "ja", "zh"), ("ja two", "ja", "zh"), ("hello", "en", "zh")]
    rep = json.loads(out.read_text("utf-8"))
    assert rep["pairs"]["ja->zh"] == {"n": 2, "chrf": 100.0} and rep["pairs"]["en->zh"]["n"] == 1
    rows = {r["id"]: r for r in rep["cases"]}
    assert [rows[i]["same_as_reported"] for i in ("issue-1", "issue-2", "issue-3")] == [False, True, True]
    assert rows["issue-1"]["chrf"] == 100.0 and rows["issue-3"]["chrf"] < 100.0
    printed = capsys.readouterr().out
    assert "ja->zh: 2 cases" in printed and "issue-2: chrF 100.0 same_as_reported true" in printed


def test_bad_input_exits_2_without_report(tmp_path, capsys):
    out = tmp_path / "r.json"
    assert main(["--text-cases", str(tmp_path / "nope.jsonl"), "--out", str(out)], engines=_engines(FakeMT())) == 2
    bad = _write(tmp_path, [{"id": "x", "pair": "ja->zh"}])
    assert main(["--text-cases", str(bad), "--out", str(out)], engines=_engines(FakeMT())) == 2
    assert "missing src, ref" in capsys.readouterr().err and not out.exists()
