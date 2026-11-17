"""#1397: default Russian acronym hotwords (Whisper only), user glossary wins, off until the eval ships them,
and the with/without acronym eval (scripts/bench/acronym_eval.py)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from tinglongwu import asr as asr_mod
from tinglongwu import glossary
from tinglongwu.hymt import HyMtTranslator, hymt_path

REPO = Path(__file__).resolve().parents[1]
ON = {glossary.DEFAULT_HOTWORDS_ENV: "1"}


def _eval():
    spec = importlib.util.spec_from_file_location("acronym_eval", REPO / "scripts" / "bench" / "acronym_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


AE = _eval()


def test_ru_list_has_30_to_60_generic_acronyms_and_all_reach_whisper():
    g = glossary.load_default_hotwords("ru")
    terms = [e.main for e in g.entries]
    assert 30 <= len(terms) <= 60 and len(set(terms)) == len(terms)
    assert all(t.isascii() and t.isupper() and t.isalpha() for t in terms)
    for t in ("API", "URL", "SQL", "JSON", "HTTP", "CPU", "GPU", "RAM", "USB", "PDF"):
        assert t in terms
    assert g.hotwords().split(", ") == terms  # the whole list fits HOTWORDS_MAX_CHARS, none cut off


def test_off_by_default_until_the_eval_ships_it_and_env_switches_it():
    assert "ru" not in glossary.DEFAULT_HOTWORDS_SHIPPED
    assert glossary.default_hotwords_on("ru", env={}) is False
    assert glossary.default_hotwords_on("ru", env=ON) is True
    assert glossary.default_hotwords_on("ru-RU", env=ON) is True
    for lang in ("auto", "en", "ja", None):
        assert glossary.default_hotwords_on(lang, env=ON) is False
    assert glossary.with_default_hotwords(None, "ru", env={}) is None
    assert glossary.with_default_hotwords(None, "ru", face_to_face="zh", env=ON) is None


def test_user_glossary_adds_to_and_wins_over_the_defaults():
    user = glossary.Glossary(glossary.parse("роутер\nAPI = 介面 ~ 阿皮\n"))
    g = glossary.with_default_hotwords(user, "ru", env=ON)
    hot = g.hotwords().split(", ")
    assert hot[:2] == ["роутер", "API"] and hot.count("API") == 1 and "SQL" in hot
    api = [e for e in g.entries if "API" in e.sources]
    assert len(api) == 1 and api[0].target == "介面" and api[0].wrong == ["阿皮"] and api[0].mt_hint


def test_builtin_mt_entry_keeps_its_translation_and_becomes_a_hotword():
    base = glossary.Glossary(glossary.parse("CPU = 處理器\nkernel = 核心\n"))
    for e in base.entries:
        e.asr_hint = False
    g = glossary.add_hotwords(base, glossary.load_default_hotwords("ru"))
    cpu = [e for e in g.entries if "CPU" in e.sources]
    assert len(cpu) == 1 and cpu[0].target == "處理器" and cpu[0].asr_hint
    assert "kernel" not in g.hotwords() and "CPU" in g.hotwords()
    assert base.entries[0].asr_hint is False  # the loaded built-in list is not changed in place


def test_defaults_never_reach_the_mt_prompt(tmp_path):
    g = glossary.load_default_hotwords("ru")
    assert g.relevant("Проверь API и SQL") == [] and g.prompt_block("Проверь API и SQL") == ""
    p = hymt_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    calls = []

    class Llm:
        def create_chat_completion(self, messages, **kw):
            calls.append(messages[0]["content"])
            return {"choices": [{"message": {"content": "好"}}]}

    tr = HyMtTranslator(tmp_path, "tw", "cpu", loader=lambda p, g: Llm())
    tr.translate("Проверь API и SQL.", "ru", "zh")
    plain = calls[-1]
    tr.glossary = g
    tr.translate("Проверь API и SQL сейчас.", "ru", "zh")
    assert "Проверь API и SQL сейчас." in calls[-1]
    assert calls[-1].replace(" сейчас", "") == plain  # same prompt as without the list


class _Model:
    def __init__(self):
        self.kw = []

    def transcribe(self, audio, **kw):
        self.kw.append(kw)
        return iter([]), SimpleNamespace(language="ru")


def test_whisper_gets_the_defaults_as_hotwords():
    r = asr_mod.FasterWhisperRecognizer.__new__(asr_mod.FasterWhisperRecognizer)
    r.model, r.lang, r.candidates, r.glossary = _Model(), "ru", None, glossary.with_default_hotwords(None, "ru", env=ON)
    r.partial(np.zeros(16000, np.float32))
    assert r.model.kw[-1]["hotwords"].startswith("API, URL, SQL")
    r.glossary = glossary.with_default_hotwords(None, "ru", env={})
    r.partial(np.zeros(16000, np.float32))
    assert r.model.kw[-1]["hotwords"] is None


# ---- eval ----

def test_eval_set_has_one_clip_per_acronym_plus_distractors():
    rows = AE.clips()
    terms = [r for r in rows if r["term"]]
    assert len(terms) >= 30 and {r["term"] for r in terms} == {e.main for e in glossary.load_default_hotwords("ru").entries}
    for r in terms:
        assert AE.asr_hit(r["term"], r["ref_text"]) and r["term"] not in r["say"], r
    for r in rows:
        if not r["term"]:
            assert AE.inserted(r["ref_text"]) == []
    assert not AE.asr_hit("HTTP", "по HTTPS") and not AE.asr_hit("ID", "в своей IDE")


def test_wer_counts():
    assert AE.wer_counts("a b c", "a b c") == (0, 3)
    assert AE.wer_counts("a b c", "a x") == (2, 3)


class _FakeAsr:
    """Hears acronyms only with hotwords; with them it also turns "Ай" into "AI ID" (a false insertion)."""

    def __init__(self):
        self.glossary, self._last, self.seen = None, "stale", []

    def transcribe(self, x):
        self.seen.append((self.glossary.hotwords() if self.glossary else None, self._last))
        r = x["row"]
        if not self.glossary:
            return SimpleNamespace(text=r["say"])
        return SimpleNamespace(text=r["ref_text"].replace("Ай,", "ID,"))


def test_run_and_summarize_measure_with_vs_without():
    rows = [r for r in AE.clips() if r["term"] in ("API", "SQL")] + [
        r for r in AE.clips() if r["ref_text"].startswith("Ай,")]
    asr = _FakeAsr()
    res = AE.run_clips(rows, asr, glossary.load_default_hotwords("ru"),
                       lambda r: {"row": r, "__len__": None} and _Audio(r))
    assert all(last == "" for _, last in asr.seen) and [h is None for h, _ in asr.seen[:2]] == [True, False]
    s = AE.summarize(res)
    assert s["no"]["acronym_acc"] == 0.0 and s["hw"]["acronym_acc"] == 1.0
    assert (s["no"]["false_insertions"], s["hw"]["false_insertions"]) == (0, 1)
    assert s["helps"] is False  # better accuracy, but an extra false insertion: not shipped
    assert "acronym accuracy | 0.0% | 100.0% | +100.0 pt" in AE.table(s)
    clean = [dict(d, asr_hw=d["ref_text"]) for d in res]
    assert AE.summarize(clean)["helps"] is True


class _Audio(dict):
    def __init__(self, r):
        super().__init__(row=r)

    def __len__(self):
        return 16000
