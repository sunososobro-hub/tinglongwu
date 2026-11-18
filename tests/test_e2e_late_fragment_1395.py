"""#1395 e2e check: the --save JSONL of 'sentence, ~1 s pause, last word' must hold one merged line, no one-word line.

The JSONL records are written by the real JsonlSaveSink fed by the real SentenceMerger, so the before/after cases
come from the product code, not from hand-made dicts. Example sentences are the made-up ones in scripts/ci_e2e.py."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from tinglongwu.merge import SentenceMerger
from tinglongwu.sink import JsonlSaveSink, Result, Sink

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ci_e2e_1395", ROOT / "scripts" / "ci_e2e.py")
ci = importlib.util.module_from_spec(_spec)
sys.modules["ci_e2e_1395"] = ci
_spec.loader.exec_module(ci)


class _Quiet(Sink):
    def write(self, r: Result) -> None:
        pass


def _save(tmp_path: Path, lang: str, amend_window: float) -> list[dict]:
    """Head (whisper adds a final period) at t=0, the last word 1.2 s later, through merger + --save sink."""
    head, last = ci.LATE[lang][:2]
    m = SentenceMerger(min_words=3, amend_window=amend_window)
    units = m.add(head + ".", lang, 0.0) + m.add(last[:1].upper() + last[1:], lang, 1.2) + m.flush()
    path = tmp_path / f"{lang}.jsonl"
    sink = JsonlSaveSink(_Quiet(), path)
    for u in units:
        sink.write(Result(src=lang, tgt="zh", original=u.text, pivot=None, target="譯文", replaces=u.replaces))
    sink.f.close()
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_late_fragment_merged_passes(tmp_path):
    for lang in ("en", "ru"):
        recs = _save(tmp_path, lang, amend_window=3.0)
        assert len(recs) == 1
        assert all(ok for _, ok, _ in ci.check_late_jsonl(recs, lang)), ci.check_late_jsonl(recs, lang)


def test_late_fragment_left_alone_fails(tmp_path):
    """Before #1395 (amend off) the last word is its own one-word line: the e2e check must fail."""
    for lang in ("en", "ru"):
        recs = _save(tmp_path, lang, amend_window=0.0)
        assert len(recs) == 2
        res = {name: ok for name, ok, _ in ci.check_late_jsonl(recs, lang)}
        assert not res[f"{lang}: no one-word line in --save (#1395)"]
        assert not res[f"{lang}: sentence + late last word saved as one line, once (#1395)"]


def test_duplicate_merged_line_fails():
    rec = {"src": "Now I will show you the settings for the new version.", "dst": "x"}
    res = {name: ok for name, ok, _ in ci.check_late_jsonl([rec, dict(rec)], "en")}
    assert not res["en: sentence + late last word saved as one line, once (#1395)"]


def test_empty_save_fails():
    assert not any(ok for _, ok, _ in ci.check_late_jsonl([], "ru"))



def test_head_line_left_next_to_merged_line_fails():
    """An amend that appended instead of replacing: head line + merged line -> must fail."""
    recs = [{"src": "Now I will show you the settings for the new.", "dst": "現在我給你看新的設定。"},
            {"src": "Now I will show you the settings for the new version.", "dst": "現在我給你看新版本的設定。"}]
    res = {name: ok for name, ok, _ in ci.check_late_jsonl(recs, "en")}
    assert not res["en: sentence + late last word saved as one line, once (#1395)"]


def test_mt_status_text_is_not_a_translation():
    src = "Сейчас я покажу вам настройки для новой версии."
    for dst in ("[未翻譯]", "［未翻譯］", "[не переведено]", "[no ru -> zh translation model installed: run tlwu-setup]", "", src):
        res = {name: ok for name, ok, _ in ci.check_late_jsonl([{"src": src, "dst": dst}], "ru")}
        assert not res["ru: the merged line is translated (#1395)"], dst
    res = {name: ok for name, ok, _ in ci.check_late_jsonl([{"src": src, "dst": "現在我給您看新版本的設定。"}], "ru")}
    assert all(res.values())



def test_head_alone_is_not_a_merged_line():
    """Only the head saved (the late word lost): one line, no one-word line, but nothing was joined -> must fail."""
    rec = {"src": "Now I will show you the settings for the new.", "dst": "現在我給你看新的設定。"}
    res = {name: ok for name, ok, _ in ci.check_late_jsonl([rec], "en")}
    assert not res["en: sentence + late last word saved as one line, once (#1395)"]


def test_misheard_late_word_still_counts_as_joined():
    """CI run 2026-11-18: Whisper heard the synthetic 'версии' as 'иерсия'; the line was still merged once."""
    rec = {"src": "Сейчас я покажу вам настройки для новой иерсия.", "dst": "現在我給您看新版本的設定。"}
    assert all(ok for _, ok, _ in ci.check_late_jsonl([rec], "ru"))
