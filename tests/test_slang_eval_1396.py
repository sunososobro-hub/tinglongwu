"""#1396: slang eval (with vs without the built-in ru list) scores what the glossary actually changes."""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "bench"))


def _load():
    spec = importlib.util.spec_from_file_location("slang_eval_1396", ROOT / "scripts" / "bench" / "slang_eval.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


se = _load()


class FakeMT:
    """Renders a term the way the prompt asks ("Use these translations: x = y") and leaves it as is otherwise,
    so the eval measures exactly whether the glossary reaches the prompt."""

    def __init__(self):
        self.glossary = None
        self.history = []

    def translate(self, text, src, tgt):
        out = text
        if self.glossary:
            for e in self.glossary.relevant(text):
                out += f" [{e.target}]"
        return types.SimpleNamespace(text=out)


class FakeASR:
    def transcribe(self, x):
        return types.SimpleNamespace(text=x)


def test_every_sentence_triggers_its_term_only_with_the_ru_list():
    g_no, g_ru = se.glossaries()
    targets = se.entries()
    assert len(se.SLANG) >= 20
    for term, sents in se.SLANG:
        assert targets.get(term), term
        for s in sents:
            assert term in [e.main for e in g_ru.relevant(s)], s
            assert term not in [e.main for e in g_no.relevant(s)], s


def test_run_and_summary_with_vs_without():
    g_no, g_ru = se.glossaries()
    rows = se.clips()
    res = se.run_clips(rows, FakeASR(), FakeMT(), g_no, g_ru, lambda r: r["say"])
    s = se.summarize(res, se.entries())
    assert s["clips"] == 2 * len(se.SLANG)
    assert s["no"]["zh_acc"] == 0.0 and s["gl"]["zh_acc"] == 1.0
    assert s["no"]["zh_gold_acc"] == 0.0 and s["gl"]["zh_gold_acc"] == 1.0
    assert "| zh term accuracy (end to end) | 0.0% | 100.0% | +100.0 pt |" in se.table(s, None)


def test_flores_only_touched_sentences_are_translated_twice():
    g_no, g_ru = se.glossaries()
    sents = ["Погода в горах была хорошей.", "Мы выкатили релиз в пятницу.", "Он читал книгу у окна."]
    assert se.affected(sents, g_no, g_ru) == [1]


def test_flores_summary_no_drop_rule():
    rows = [{"ref": "a", "no": "x", "gl": "y"}]
    up = se.flores_summary(rows, {"total": 1012, "affected": 1}, chrf=lambda h, r: 50.0 if h == ["y"] else 40.0)
    assert up["no_drop"] and up["changed"] == 1
    down = se.flores_summary(rows, {"total": 1012, "affected": 1}, chrf=lambda h, r: 30.0 if h == ["y"] else 40.0)
    assert not down["no_drop"]
    assert se.flores_summary([], {"total": 1012, "affected": 0})["no_drop"]


def test_report_end_to_end(tmp_path, capsys):
    g_no, g_ru = se.glossaries()
    res = se.run_clips(se.clips(), FakeASR(), FakeMT(), g_no, g_ru, lambda r: r["say"])
    (tmp_path / "slang.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in res), "utf-8")
    (tmp_path / "flores.json").write_text(json.dumps({"total": 1012, "affected": 0}), "utf-8")
    (tmp_path / "flores.jsonl").write_text("", "utf-8")
    assert se.main(["report", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "0/1012 sentences touched" in out and "| коммит | 0/2 | 2/2 |" in out
    assert json.loads((tmp_path / "summary.json").read_text("utf-8"))["terms"]["gl"]["zh_acc"] == 1.0
