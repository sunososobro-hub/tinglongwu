"""#373: meeting mode must tell the owner's mic (我) from the other side's system audio (對方)."""
import io
import json

import pytest

from tinglongwu import fmt
from tinglongwu.sink import JsonlSaveSink, Result, TextSink


@pytest.mark.parametrize("lang,me,them", [("en", "Me", "Them"), ("ru", "Я", "Собеседник"), ("zh-TW", "我", "對方")])
def test_labels_differ_per_side(monkeypatch, lang, me, them):
    monkeypatch.setenv("TLWU_LANG", lang)
    monkeypatch.setattr(fmt, "t", lambda k: fmt.MESSAGES[lang][k])
    a, b = fmt.head("me", "x", color=True), fmt.head("them", "y", color=True)
    assert me in a and them not in a and them in b and me not in b
    assert fmt.GREEN in a and fmt.CYAN in b


def test_sink_and_save_file_carry_side(tmp_path, monkeypatch):
    monkeypatch.setattr(fmt, "t", lambda k: fmt.MESSAGES["zh-TW"][k])
    out = io.StringIO()
    s = JsonlSaveSink(TextSink(out), tmp_path / "t.jsonl")
    s.write(Result("zh", "我說的", None, "I said", "en", side="me"))
    s.write(Result("en", "they said", None, "他們說", "zh"))
    text = out.getvalue()
    assert text.index("我說的") > text.index("▶ 我") and text.index("they said") > text.index("◀ 對方")
    recs = [json.loads(x) for x in (tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["side"] for r in recs] == ["me", "them"]
